"""Pruebas en Linux (sin Windows, sin micrófono) de lo nuevo en voz.py y pc_mcp.py:
avisos proactivos, estado para el HUD, atajo de teclado, control_pc, abrir_url y rutinas.

    python3 tests/test_voz_y_pc.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cargar(nombre, ruta):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))   # sin PortAudio en Linux
voz = _cargar("voz_prueba", os.path.join(RAIZ, "voz", "voz.py"))
pc = _cargar("pc_mcp_prueba", os.path.join(RAIZ, "herramientas", "pc_mcp.py"))


# ------------------------------------------------------------------ voz.py: avisos
class Avisos(unittest.TestCase):
    def setUp(self):
        self.ahora = 1_800_000_000.0

    def aviso(self, id_, prioridad="normal", hace=10, **extra):
        return {"id": id_, "texto": f"aviso {id_}", "cuando": self.ahora - hace,
                "prioridad": prioridad, "leido": False, **extra}

    def test_despierto_dice_todos_alta_primero(self):
        lista = [self.aviso("a"), self.aviso("b", "alta"), self.aviso("c", leido=True)]
        decir, marcas = voz.elegir_avisos(lista, False, self.ahora, set())
        self.assertEqual([a["id"] for a in decir], ["b", "a"])
        self.assertEqual(marcas, {"a": {"leido": True}, "b": {"leido": True}})

    def test_dormido_solo_alta(self):
        lista = [self.aviso("a"), self.aviso("b", "alta")]
        decir, marcas = voz.elegir_avisos(lista, True, self.ahora, set())
        self.assertEqual([a["id"] for a in decir], ["b"])
        self.assertNotIn("a", marcas)          # el normal queda sin leer para cuando despierte

    def test_futuro_viejo_repetido_y_basura(self):
        lista = [self.aviso("futuro", hace=-600), self.aviso("viejo", hace=13 * 3600),
                 self.aviso("dicho"), self.aviso("dup"), self.aviso("dup"), "basura", {"texto": "sin id"},
                 {"id": "vacio", "texto": "  ", "leido": False}]
        decir, marcas = voz.elegir_avisos(lista, False, self.ahora, {"dicho"}, max_horas=12)
        self.assertEqual([a["id"] for a in decir], ["dup"])
        self.assertNotIn("futuro", marcas)
        self.assertEqual(marcas["viejo"], {"leido": True, "caducado": True})
        self.assertEqual(marcas["dicho"], {"leido": True})
        self.assertEqual(marcas["vacio"], {"leido": True})

    def test_fusion_no_pierde_avisos_nuevos(self):
        fresca = [self.aviso("a"), self.aviso("nuevo"), "raro"]
        res = voz.fusionar_avisos(fresca, {"a": {"leido": True}, "borrado": {"leido": True}})
        self.assertEqual([x["id"] if isinstance(x, dict) else x for x in res], ["a", "nuevo", "raro"])
        self.assertTrue(res[0]["leido"])
        self.assertFalse(res[1]["leido"])

    def test_revisar_avisos_con_escritura_concurrente(self):
        carpeta = tempfile.mkdtemp()
        ruta = os.path.join(carpeta, "avisos.json")
        ahora = time.time()
        inicial = [{"id": "1", "texto": "El deploy de CHECK falló.", "cuando": ahora - 5,
                    "prioridad": "alta", "leido": False}]
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(inicial, f)
        original_leer, lecturas = voz._leer_json, []

        def leer(r):   # el cerebro agrega un aviso entre mi primera lectura y la escritura
            datos = original_leer(r)
            lecturas.append(1)
            if len(lecturas) == 2:
                datos = datos + [{"id": "2", "texto": "nuevo", "cuando": ahora, "prioridad": "normal",
                                  "leido": False}]
            return datos

        viejos = (voz.AVISOS, voz._leer_json)
        try:
            voz.AVISOS, voz._leer_json = ruta, leer
            voz._AVISOS_DICHOS.clear()
            while not voz.eventos.empty():
                voz.eventos.get()
            voz.revisar_avisos()
        finally:
            voz.AVISOS, voz._leer_json = viejos
        with open(ruta, encoding="utf-8") as f:
            final = json.load(f)
        self.assertEqual([(a["id"], a["leido"]) for a in final], [("1", True), ("2", False)])
        self.assertEqual(voz.eventos.get_nowait(), ("aviso", "El deploy de CHECK falló.", True))
        self.assertEqual([n for n in os.listdir(carpeta) if n.endswith(".tmp")], [])
        shutil.rmtree(carpeta)


# ------------------------------------------------------------------ voz.py: estado para el HUD
class Estado(unittest.TestCase):
    def setUp(self):
        self.carpeta = tempfile.mkdtemp()
        self.viejo = voz.ESTADO_ARCHIVO
        voz.ESTADO_ARCHIVO = os.path.join(self.carpeta, "tmp", "estado.json")
        voz._ESTADO.update({"estado": "apagado", "usuario": "", "pedro": "", "t": 0.0})
        voz._ESTADO_ULTIMO[0] = 0.0
        voz._ESTADO_PENDIENTE[0] = False

    def tearDown(self):
        voz.ESTADO_ARCHIVO = self.viejo
        shutil.rmtree(self.carpeta)

    def leer(self):
        with open(voz.ESTADO_ARCHIVO, encoding="utf-8") as f:
            return json.load(f)

    def test_esquema_y_limite_de_escrituras(self):
        escrituras = []
        original = voz._escribir_json
        voz._escribir_json = lambda ruta, datos, indent=None: (escrituras.append(datos), original(ruta, datos))[1]
        try:
            voz.poner_estado("pensando", usuario="¿qué hora es?", pedro="")
            for i in range(50):                       # streaming rápido de texto parcial
                voz.poner_estado(pedro="Son las " + "x" * i)
            self.assertLessEqual(len(escrituras), 2)
            voz.poner_estado("hablando")              # cambio de estado: se escribe al instante
            self.assertEqual(self.leer()["estado"], "hablando")
            self.assertEqual(self.leer()["pedro"], "Son las " + "x" * 49)
            voz.poner_estado(pedro="y" * 1000)        # pendiente por el límite…
            time.sleep(0.25)
            voz._latido()                             # …y el latido lo escribe
        finally:
            voz._escribir_json = original
        datos = self.leer()
        self.assertEqual(set(datos), {"estado", "usuario", "pedro", "t"})
        self.assertEqual(datos["usuario"], "¿qué hora es?")
        self.assertEqual(len(datos["pedro"]), 400)
        self.assertIsInstance(datos["t"], float)

    def test_apagado_al_salir(self):
        voz._al_salir()                               # nunca escribió: no toca el archivo
        self.assertFalse(os.path.exists(voz.ESTADO_ARCHIVO))
        voz.poner_estado("escuchando")
        voz._DORMIDO[0] = True
        voz.estado_reposo()
        self.assertEqual(self.leer()["estado"], "dormido")
        voz._DORMIDO[0] = False
        voz._al_salir()
        self.assertEqual(self.leer()["estado"], "apagado")
        self.assertIn(self.leer()["estado"], voz.ESTADOS)


# ------------------------------------------------------------------ voz.py: atajo y resumen
class Atajo(unittest.TestCase):
    def test_parseo(self):
        self.assertEqual(voz.parsear_atajo("ctrl+alt+p"), (0x2 | 0x1, ord("P")))
        self.assertEqual(voz.parsear_atajo(" Ctrl + Shift + F9 "), (0x2 | 0x4, 0x78))
        self.assertEqual(voz.parsear_atajo("f12"), (0, 0x7B))
        self.assertEqual(voz.parsear_atajo("win+space"), (0x8, 0x20))
        for apagado in ("", "no", "0", "off"):
            self.assertIsNone(voz.parsear_atajo(apagado))
        for malo in ("p", "ctrl+", "ctrl+alt+ñ", "hyper+p", "ctrl+alt+pp"):
            with self.assertRaises(ValueError, msg=malo):
                voz.parsear_atajo(malo)

    def test_sin_windows_no_rompe(self):
        voz.iniciar_atajo()   # en Linux solo avisa
        self.assertFalse(voz._ATAJO_ACTIVO[0])

    def test_prompt_resumen_en_orden(self):
        p = voz.PROMPT_RESUMEN
        posiciones = [p.index(x) for x in ("agenda", "correos_recientes", "estado_vigilancia",
                                           "listar_recordatorios", "git")]
        self.assertEqual(posiciones, sorted(posiciones))


# ------------------------------------------------------------------ pc_mcp.py: control_pc
class ControlPC(unittest.TestCase):
    def setUp(self):
        self.ps, self.lanzados, self.exes = [], [], []
        self.codigo = 0
        self.viejos = (pc._powershell, pc._lanzar, pc._ejecutar)
        pc._powershell = lambda args, script=pc._PS1: (self.ps.append((args, script)), "OK")[1]
        pc._lanzar = lambda argv: self.lanzados.append(argv)
        pc._ejecutar = lambda argv, timeout=15: (self.exes.append(argv), (self.codigo, ""))[1]

    def tearDown(self):
        pc._powershell, pc._lanzar, pc._ejecutar = self.viejos

    def test_volumen(self):
        pc.control_pc("volumen", "50%")
        pc.control_pc("Subir volumen")
        pc.control_pc("bajar_volumen", 20)
        self.assertEqual(self.ps, [
            (["-Accion", "volumen", "-Valor", "50"], pc._PS_SISTEMA),
            (["-Accion", "subir_volumen", "-Valor", "10"], pc._PS_SISTEMA),
            (["-Accion", "bajar_volumen", "-Valor", "20"], pc._PS_SISTEMA)])
        self.assertTrue(pc._PS_SISTEMA.endswith("sistema.ps1"))

    def test_valores_malos(self):
        for accion, valor in [("volumen", None), ("volumen", 150), ("volumen", -1), ("volumen", True),
                              ("volumen", "mucho"), ("volumen", float("nan")), ("apagar_en", 0),
                              ("apagar_en", None), ("apagar_en", 2000), ("formatear", None), ("", None)]:
            with self.assertRaises(ValueError, msg=(accion, valor)):
                pc.control_pc(accion, valor)
        self.assertEqual((self.ps, self.lanzados, self.exes), ([], [], []))

    def test_teclas_bloqueo_y_apagado(self):
        pc.control_pc("Música pausa")
        pc.control_pc("silenciar")
        pc.control_pc("pantalla_apagar")
        self.assertEqual([a for a, _ in self.ps], [["-Accion", "musica_pausa"], ["-Accion", "silenciar"],
                                                   ["-Accion", "pantalla_apagar"]])
        pc.control_pc("bloquear")
        self.assertEqual(self.lanzados, [["rundll32.exe", "user32.dll,LockWorkStation"]])
        self.assertIn("30 minuto", pc.control_pc("apagar_en", "30"))
        self.assertEqual(self.exes[-1], ["shutdown.exe", "/s", "/t", "1800"])
        self.codigo = 1190
        with self.assertRaises(RuntimeError):
            pc.control_pc("apagar_en", 5)
        self.codigo = 1116
        self.assertIn("No había", pc.control_pc("cancelar_apagado"))
        self.assertEqual(self.exes[-1], ["shutdown.exe", "/a"])

    def test_herramientas_registradas(self):
        for nombre in ("control_pc", "abrir_url", "rutina", "listar_rutinas"):
            self.assertIn(nombre, pc.HERRAMIENTAS)
            self.assertTrue(pc.HERRAMIENTAS[nombre][1]["description"])


# ------------------------------------------------------------------ pc_mcp.py: abrir_url
class AbrirURL(unittest.TestCase):
    def setUp(self):
        self.lanzados, self.viejo = [], pc._lanzar
        pc._lanzar = lambda argv: self.lanzados.append(argv)

    def tearDown(self):
        pc._lanzar = self.viejo

    def test_validas(self):
        pc.abrir_url("https://vercel.com/dashboard?a=1&b=2#x")
        pc.abrir_url("supabase.com")
        pc.abrir_url("HTTPS://github.com/onnyx")
        self.assertEqual(self.lanzados, [["explorer.exe", "https://vercel.com/dashboard?a=1&b=2#x"],
                                         ["explorer.exe", "https://supabase.com"],
                                         ["explorer.exe", "https://github.com/onnyx"]])

    def test_invalidas(self):
        for url in ["http://vercel.com", "javascript:alert(1)", "file:///C:/Windows", "https://a b.com",
                    "https://x.com/a,b", 'https://x.com/"x', "https://user:pw@x.com", "https://localhost:3000",
                    "C:\\Windows\\system32\\calc.exe", "", "https://x.com/\nhola"]:
            with self.assertRaises(ValueError, msg=url):
                pc.abrir_url(url)
        self.assertEqual(self.lanzados, [])


# ------------------------------------------------------------------ pc_mcp.py: rutinas
class Rutinas(unittest.TestCase):
    def setUp(self):
        self.carpeta = tempfile.mkdtemp()
        self.ruta = os.path.join(self.carpeta, "rutinas.json")
        shutil.copy(os.path.join(RAIZ, "rutinas.example.json"), self.ruta)
        os.environ["PEDRO_RUTINAS"] = self.ruta
        self.llamadas = []
        self.viejos = {n: getattr(pc, n) for n in ("abrir_proyecto", "abrir_app", "abrir_terminal",
                                                   "abrir_claude_code", "control_pc", "abrir_url", "_esperar")}
        for n in self.viejos:
            setattr(pc, n, (lambda nombre: lambda **kw: (self.llamadas.append((nombre, kw)), f"{nombre} ok.")[1])(n))
        self.sleep, pc.time.sleep = pc.time.sleep, lambda s: None

    def tearDown(self):
        for n, f in self.viejos.items():
            setattr(pc, n, f)
        pc.time.sleep = self.sleep
        os.environ.pop("PEDRO_RUTINAS", None)
        shutil.rmtree(self.carpeta)

    def escribir(self, datos):
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump(datos, f)

    def test_nombres_aproximados(self):
        rutinas = pc._cargar_rutinas()
        casos = {"modo trabajo": "trabajo", "Trabajo": "trabajo", "a trabajar": "trabajo",
                 "Fin del día": "fin del dia", "la rutina de fin de dia": "fin del dia", "REUNIÓN": "reunion",
                 "modo reunión": "reunion", "llamada": "reunion", "trabaj": "trabajo"}
        for pedido, esperado in casos.items():
            self.assertEqual(pc._buscar_rutina(pedido, rutinas), esperado, pedido)
        with self.assertRaises(ValueError):
            pc._buscar_rutina("karaoke", rutinas)
        with self.assertRaises(ValueError):
            pc._buscar_rutina("", rutinas)

    def test_ejecuta_ejemplo_en_orden(self):
        res = pc.rutina("modo reunión")
        self.assertEqual(self.llamadas, [("control_pc", {"accion": "musica_pausa"}),
                                         ("control_pc", {"accion": "silenciar"})])
        self.assertIn("reunion", res)
        self.llamadas.clear()
        pc.rutina("trabajo")
        self.assertEqual([n for n, _ in self.llamadas],
                         ["abrir_proyecto", "abrir_terminal", "abrir_claude_code", "abrir_app"])
        self.llamadas.clear()
        pc.rutina("fin del día")
        self.assertEqual(self.llamadas[-1], ("control_pc", {"accion": "bloquear"}))
        self.assertIn("trabajo", pc.listar_rutinas())

    def test_un_paso_que_falla_no_detiene_el_resto(self):
        def falla(**kw):
            raise ValueError("No encontré el proyecto 'check'.")
        pc.abrir_proyecto = falla
        res = pc.rutina("trabajo")
        self.assertIn("Fallaron 1 paso", res)
        self.assertEqual([n for n, _ in self.llamadas], ["abrir_terminal", "abrir_claude_code", "abrir_app"])

    def test_lista_blanca_y_validacion_previa(self):
        malos = [
            [{"accion": "abrir_app", "app": "spotify"}, {"accion": "bash", "comando": "del /s C:\\"}],
            [{"accion": "abrir_app", "app": "spotify"}, {"accion": "control_pc", "control": "apagar_en", "valor": 1}],
            [{"accion": "abrir_app", "app": "spotify", "argv": ["cmd.exe"]}],
            [{"accion": "abrir_app", "app": ["calc.exe"]}],
            [{"accion": "control_pc", "control": "formatear"}],
            [{"accion": "esperar", "segundos": 15}, {"accion": "esperar", "segundos": 15},
             {"accion": "esperar", "segundos": 15}],
            [{"accion": "abrir_app", "app": "spotify"}] * 16,
            [], "abrir spotify",
        ]
        for pasos in malos:
            self.escribir({"mala": {"pasos": pasos}})
            with self.assertRaises(ValueError, msg=pasos):
                pc.rutina("mala")
            self.assertEqual(self.llamadas, [], pasos)   # no ejecutó NADA antes de validar todo

    def test_formato_args_y_sin_archivo(self):
        self.escribir({"_comentario": "x", "musica": [{"accion": "control_pc", "args": {"accion": "musica_siguiente"}}]})
        pc.rutina("música")
        self.assertEqual(self.llamadas, [("control_pc", {"accion": "musica_siguiente"})])
        os.remove(self.ruta)
        self.assertIn("rutinas.example.json", pc.listar_rutinas())
        with self.assertRaises(ValueError):
            pc.rutina("trabajo")


# ------------------------------------------------------------------ sistema.ps1 (sintaxis y parámetros)
PWSH = shutil.which("pwsh") or ("/opt/pwsh/pwsh" if os.path.exists("/opt/pwsh/pwsh") else None)


@unittest.skipUnless(PWSH, "PowerShell no instalado")
class SistemaPS1(unittest.TestCase):
    ps1 = os.path.join(RAIZ, "herramientas", "sistema.ps1")

    def correr(self, *args):
        return subprocess.run([PWSH, "-NoProfile", "-File", self.ps1, *args], capture_output=True, text=True,
                              timeout=60)

    def test_rechaza_parametros(self):
        self.assertNotEqual(self.correr("-Accion", "formatear").returncode, 0)
        self.assertNotEqual(self.correr("-Accion", "volumen", "-Valor", "150").returncode, 0)

    def test_sin_cosas_sospechosas(self):
        with open(self.ps1, encoding="utf-8-sig") as f:
            texto = f.read().lower()
        for palabra in ("invoke-expression", "iex", "downloadstring", "invoke-webrequest", "frombase64",
                        "start-process", "-encodedcommand"):
            self.assertNotIn(palabra, texto)


if __name__ == "__main__":
    unittest.main(verbosity=2)
