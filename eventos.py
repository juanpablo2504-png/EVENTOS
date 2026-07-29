import streamlit as st
import pandas as pd
import plotly.express as px
import json
import random
import string
import io
import smtplib
import base64
import psycopg2
import psycopg2.extras
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

st.set_page_config(page_title="Eventos & Boletos", page_icon="🎟️", layout="wide")

ZONA_CDMX    = ZoneInfo("America/Mexico_City")
COLUMNA_NB   = "N° de boletos"
COLUMNA_TIPO = "Tipo de boleto"

COLUMNAS_INVITADOS_DEFAULT = [
    {"nombre": "Nombre completo",    "requerido": True},
    {"nombre": "Correo electrónico", "requerido": True},
    {"nombre": "Teléfono",           "requerido": True},
    {"nombre": "Empresa",            "requerido": True},
    {"nombre": "Puesto",             "requerido": True},
]
CAMPOS_DESGLOSE_DEFAULT = ["Empresa"]
AREAS_DEFAULT = [
    "Comercial", "Legal y Finanzas", "CES", "MKT", "Capital Humano", "Arrendadoras",
    "Fundación", "Inter.mx", "Beneficios", "Premium", "Middle Market", "Especialidades",
    "Reasinter", "Alianzas", "Técnico", "Siniestros", "Servicios Generales", "Otro",
]
DIAS_ES  = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]
MESES_ES = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]


def hoy_cdmx():
    return datetime.now(ZONA_CDMX).date()

def ahora_cdmx():
    return datetime.now(ZONA_CDMX).isoformat()

def fecha_bonita(fecha_str):
    try:
        d = date.fromisoformat(str(fecha_str))
        return f"{DIAS_ES[d.weekday()]} {d.day} {MESES_ES[d.month-1]} {d.year}"
    except Exception:
        return str(fecha_str)


# ════════════════════════════════════════════
# BASE DE DATOS (PostgreSQL / Supabase)
# ════════════════════════════════════════════

def get_conn():
    try:
        url = st.secrets["database"]["url"]
        conn = psycopg2.connect(url)
        return conn
    except KeyError:
        st.error(
            "⚠️ No hay conexión a la base de datos configurada. "
            "Ve a los Secrets de Streamlit y agrega:\n\n"
            "```toml\n[database]\nurl = \"postgresql://...\"\n```"
        )
        st.stop()
    except Exception as e:
        st.error(f"Error de conexión a la base de datos: {e}")
        st.stop()


def _col_exists(conn, table, col):
    c = conn.cursor()
    c.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name=%s AND column_name=%s",
        (table, col)
    )
    return c.fetchone() is not None


def _add_col(conn, table, col, coltype):
    if not _col_exists(conn, table, col):
        c = conn.cursor()
        c.execute(f'ALTER TABLE {table} ADD COLUMN {col} {coltype}')
        conn.commit()


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS configuracion (
        clave TEXT PRIMARY KEY,
        valor TEXT NOT NULL
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS eventos (
        id SERIAL PRIMARY KEY,
        nombre TEXT NOT NULL,
        fecha TEXT NOT NULL,
        hora TEXT,
        venue TEXT,
        descripcion TEXT,
        imagen_data TEXT,
        publicado INTEGER DEFAULT 0,
        max_dias_anticipacion INTEGER,
        min_dias_anticipacion INTEGER,
        creado_en TEXT NOT NULL
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS tipos_boleto (
        id SERIAL PRIMARY KEY,
        evento_id INTEGER NOT NULL,
        nombre TEXT NOT NULL,
        capacidad INTEGER NOT NULL,
        max_por_solicitud INTEGER
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS reservas (
        id SERIAL PRIMARY KEY,
        evento_id INTEGER NOT NULL,
        tipo_boleto_id INTEGER NOT NULL,
        tipo_boleto_nombre TEXT NOT NULL,
        cantidad INTEGER NOT NULL,
        comentario TEXT,
        creado_en TEXT NOT NULL,
        codigo TEXT UNIQUE,
        estado TEXT,
        revisado_en TEXT,
        motivo_rechazo TEXT,
        lista_invitados TEXT,
        lista_subida_en TEXT,
        solicitante_nombre TEXT,
        solicitante_correo TEXT,
        desglose_inicial TEXT,
        area TEXT DEFAULT ''
    )""")

    conn.commit()

    # Migraciones: agregar columnas nuevas si no existen
    _add_col(conn, "reservas", "area", "TEXT DEFAULT ''")

    # Config por defecto
    defaults = {
        "admin_password":          "admin123",
        "max_dias_anticipacion":   "90",
        "min_dias_anticipacion":   "0",
        "max_boletos_global":      "10",
        "columnas_invitados":      json.dumps(COLUMNAS_INVITADOS_DEFAULT, ensure_ascii=False),
        "campos_desglose":         json.dumps(CAMPOS_DESGLOSE_DEFAULT, ensure_ascii=False),
        "areas":                   json.dumps(AREAS_DEFAULT, ensure_ascii=False),
        "correos_notificacion":    json.dumps(["aega@inter.mx","jpma@inter.mx","alrf@inter.mx"], ensure_ascii=False),
        "pie_contacto":            "Para cualquier duda, contacta a:\nMargarita Escobedo: aega@inter.mx\nJuan Pablo Muniain: jpma@inter.mx\nAlejandro Romano: alrf@inter.mx",
        "app_url":                 "",
    }
    for k, v in defaults.items():
        c.execute(
            "INSERT INTO configuracion (clave, valor) VALUES (%s, %s) "
            "ON CONFLICT (clave) DO NOTHING",
            (k, v)
        )

    conn.commit()
    conn.close()


# ── Config ───────────────────────────────────

def get_rule(clave, default=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT valor FROM configuracion WHERE clave=%s", (clave,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default

def set_rule(clave, valor):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO configuracion (clave,valor) VALUES (%s,%s) "
        "ON CONFLICT (clave) DO UPDATE SET valor=EXCLUDED.valor",
        (clave, str(valor))
    )
    conn.commit()
    conn.close()

def get_columnas_invitados():
    raw = get_rule("columnas_invitados")
    try:    return json.loads(raw)
    except: return [dict(c) for c in COLUMNAS_INVITADOS_DEFAULT]

def set_columnas_invitados(cols):
    set_rule("columnas_invitados", json.dumps(cols, ensure_ascii=False))

def get_campos_desglose():
    raw = get_rule("campos_desglose")
    try:    return json.loads(raw)
    except: return list(CAMPOS_DESGLOSE_DEFAULT)

def set_campos_desglose(campos):
    set_rule("campos_desglose", json.dumps(campos, ensure_ascii=False))

def get_areas():
    raw = get_rule("areas")
    try:    return json.loads(raw)
    except: return list(AREAS_DEFAULT)

def set_areas(lista):
    set_rule("areas", json.dumps(lista, ensure_ascii=False))

def get_correos_notificacion():
    raw = get_rule("correos_notificacion")
    try:    return json.loads(raw)
    except: return []

def get_pie_contacto():
    return get_rule("pie_contacto", "")

def get_app_url():
    return get_rule("app_url", "")


# ── Eventos ──────────────────────────────────

def get_eventos(solo_publicados=False):
    conn = get_conn()
    where = "WHERE publicado=1" if solo_publicados else ""
    df = pd.read_sql_query(
        f"SELECT * FROM eventos {where} ORDER BY fecha ASC", conn
    )
    conn.close()
    return df

def get_evento(evento_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM eventos WHERE id=%s", (evento_id,))
    row = c.fetchone()
    cols = [d[0] for d in c.description] if c.description else []
    conn.close()
    return dict(zip(cols, row)) if row else {}

def crear_evento(nombre, fecha, hora, venue, descripcion, imagen_data,
                 publicado, max_dias, min_dias):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO eventos (nombre,fecha,hora,venue,descripcion,imagen_data,"
        "publicado,max_dias_anticipacion,min_dias_anticipacion,creado_en) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (nombre, str(fecha), hora, venue, descripcion, imagen_data,
         int(publicado), max_dias, min_dias, ahora_cdmx())
    )
    eid = c.fetchone()[0]
    conn.commit()
    conn.close()
    return eid

def actualizar_evento(evento_id, nombre, fecha, hora, venue, descripcion,
                      imagen_data, publicado, max_dias, min_dias):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE eventos SET nombre=%s,fecha=%s,hora=%s,venue=%s,descripcion=%s,"
        "imagen_data=%s,publicado=%s,max_dias_anticipacion=%s,min_dias_anticipacion=%s "
        "WHERE id=%s",
        (nombre, str(fecha), hora, venue, descripcion, imagen_data,
         int(publicado), max_dias, min_dias, evento_id)
    )
    conn.commit()
    conn.close()

def borrar_evento(evento_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id FROM tipos_boleto WHERE evento_id=%s", (evento_id,))
    tipos = [r[0] for r in c.fetchall()]
    for tid in tipos:
        c.execute("DELETE FROM reservas WHERE tipo_boleto_id=%s", (tid,))
    c.execute("DELETE FROM tipos_boleto WHERE evento_id=%s", (evento_id,))
    c.execute("DELETE FROM eventos WHERE id=%s", (evento_id,))
    conn.commit()
    conn.close()


# ── Tipos de boleto ──────────────────────────

def get_tipos_boleto(evento_id):
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT * FROM tipos_boleto WHERE evento_id=%s ORDER BY id",
        conn, params=(evento_id,)
    )
    conn.close()
    return df

def crear_tipo_boleto(evento_id, nombre, capacidad, max_por_solicitud):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO tipos_boleto (evento_id,nombre,capacidad,max_por_solicitud) "
        "VALUES (%s,%s,%s,%s) RETURNING id",
        (evento_id, nombre, capacidad, max_por_solicitud)
    )
    tid = c.fetchone()[0]
    conn.commit()
    conn.close()
    return tid

def actualizar_tipo_boleto(tipo_id, nombre, capacidad, max_por_solicitud):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE tipos_boleto SET nombre=%s,capacidad=%s,max_por_solicitud=%s WHERE id=%s",
        (nombre, capacidad, max_por_solicitud, tipo_id)
    )
    conn.commit()
    conn.close()

def borrar_tipo_boleto(tipo_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM reservas WHERE tipo_boleto_id=%s", (tipo_id,))
    c.execute("DELETE FROM tipos_boleto WHERE id=%s", (tipo_id,))
    conn.commit()
    conn.close()


# ── Disponibilidad ───────────────────────────

def reservado_aprobado_tipo(tipo_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COALESCE(SUM(cantidad),0) FROM reservas "
              "WHERE tipo_boleto_id=%s AND estado='aprobada'", (tipo_id,))
    total = c.fetchone()[0]
    conn.close()
    return int(total)

def en_juego_tipo(tipo_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COALESCE(SUM(cantidad),0) FROM reservas "
              "WHERE tipo_boleto_id=%s AND estado IN ('pendiente','aprobada')", (tipo_id,))
    total = c.fetchone()[0]
    conn.close()
    return int(total)

def disponible_tipo(tipo_id, capacidad):
    return max(0, capacidad - en_juego_tipo(tipo_id))

def max_solicitud_tipo(tipo_id, tipo_row=None):
    global_max = int(get_rule("max_boletos_global", 10))
    tipo_max = tipo_row.get("max_por_solicitud") if tipo_row else None
    if not tipo_max:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT max_por_solicitud FROM tipos_boleto WHERE id=%s", (tipo_id,))
        row = c.fetchone()
        conn.close()
        tipo_max = row[0] if row else None
    if tipo_max:
        return min(global_max, int(tipo_max))
    return global_max


# ── Reservas ─────────────────────────────────

def generar_codigo_unico():
    conn = get_conn()
    c = conn.cursor()
    for _ in range(100):
        codigo = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        c.execute("SELECT 1 FROM reservas WHERE codigo=%s", (codigo,))
        if not c.fetchone():
            conn.close()
            return codigo
    conn.close()
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def add_reserva(evento_id, tipo_boleto_id, tipo_boleto_nombre, cantidad,
                comentario, solicitante_nombre, solicitante_correo,
                desglose_inicial, area=""):
    codigo = generar_codigo_unico()
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO reservas (evento_id,tipo_boleto_id,tipo_boleto_nombre,cantidad,"
        "comentario,creado_en,codigo,estado,solicitante_nombre,solicitante_correo,"
        "desglose_inicial,area) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (evento_id, tipo_boleto_id, tipo_boleto_nombre, cantidad,
         comentario, ahora_cdmx(), codigo, "pendiente",
         solicitante_nombre, solicitante_correo,
         json.dumps(desglose_inicial, ensure_ascii=False), area)
    )
    conn.commit()
    conn.close()
    return codigo

def crear_reserva_manual(evento_id, tipo_boleto_id, tipo_boleto_nombre, cantidad,
                         comentario, solicitante_nombre, solicitante_correo,
                         estado, codigo_forzado="", desglose_inicial=None, area=""):
    conn = get_conn()
    c = conn.cursor()
    if codigo_forzado.strip():
        codigo = codigo_forzado.strip().upper()
        c.execute("SELECT 1 FROM reservas WHERE codigo=%s", (codigo,))
        if c.fetchone():
            conn.close()
            return False, f"Ya existe la reserva con código {codigo}."
    else:
        conn.close()
        codigo = generar_codigo_unico()
        conn = get_conn()
        c = conn.cursor()
    c.execute(
        "INSERT INTO reservas (evento_id,tipo_boleto_id,tipo_boleto_nombre,cantidad,"
        "comentario,creado_en,codigo,estado,solicitante_nombre,solicitante_correo,"
        "desglose_inicial,area) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (evento_id, tipo_boleto_id, tipo_boleto_nombre, cantidad,
         comentario, ahora_cdmx(), codigo, estado,
         solicitante_nombre, solicitante_correo,
         json.dumps(desglose_inicial or [], ensure_ascii=False), area)
    )
    conn.commit()
    conn.close()
    return True, f"Reserva creada con código {codigo}."

def get_reserva_by_codigo(codigo):
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT * FROM reservas WHERE codigo=%s", conn, params=(codigo.upper(),)
    )
    conn.close()
    return df.iloc[0].to_dict() if not df.empty else None

def get_reservas(evento_id=None, estado=None):
    conn = get_conn()
    q = "SELECT * FROM reservas WHERE 1=1"
    params = []
    if evento_id:
        q += " AND evento_id=%s"
        params.append(evento_id)
    if estado:
        q += " AND estado=%s"
        params.append(estado)
    q += " ORDER BY creado_en DESC"
    df = pd.read_sql_query(q, conn, params=params if params else None)
    conn.close()
    return df

def aprobar_reserva(reserva_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT tipo_boleto_id, cantidad, estado FROM reservas WHERE id=%s",
              (reserva_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "No se encontró la reserva."
    tipo_id, cantidad, estado = row
    if estado != "pendiente":
        conn.close()
        return False, "Esta solicitud ya fue revisada."
    c.execute("SELECT capacidad FROM tipos_boleto WHERE id=%s", (tipo_id,))
    cap_row = c.fetchone()
    if not cap_row:
        conn.close()
        return False, "No se encontró el tipo de boleto."
    capacidad = int(cap_row[0])
    conn.close()
    aprobado = reservado_aprobado_tipo(tipo_id)
    if aprobado + cantidad > capacidad:
        return False, (f"Solo quedan {max(0, capacidad-aprobado)} boletos disponibles "
                       f"de este tipo. No puedes aprobar {cantidad}.")
    conn2 = get_conn()
    c2 = conn2.cursor()
    c2.execute("UPDATE reservas SET estado='aprobada', revisado_en=%s WHERE id=%s",
               (ahora_cdmx(), reserva_id))
    conn2.commit()
    conn2.close()
    return True, "Solicitud aprobada."

def rechazar_reserva(reserva_id, motivo=""):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE reservas SET estado='rechazada', revisado_en=%s, motivo_rechazo=%s WHERE id=%s",
              (ahora_cdmx(), motivo, reserva_id))
    conn.commit()
    conn.close()

def editar_cantidad_reserva(reserva_id, nueva_cantidad):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT tipo_boleto_id, cantidad, estado, lista_invitados FROM reservas WHERE id=%s",
              (reserva_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "No se encontró la reserva.", False
    tipo_id, cantidad_actual, estado, lista_inv = row
    if nueva_cantidad == cantidad_actual:
        conn.close()
        return True, "Sin cambios.", False
    if estado == "aprobada":
        c.execute("SELECT capacidad FROM tipos_boleto WHERE id=%s", (tipo_id,))
        cap_row = c.fetchone()
        if cap_row:
            aprobado_otros = reservado_aprobado_tipo(tipo_id) - cantidad_actual
            disponible = int(cap_row[0]) - aprobado_otros
            if nueva_cantidad > disponible:
                conn.close()
                return False, f"Solo hay {disponible} boletos disponibles de este tipo.", False
    tenia_lista = isinstance(lista_inv, str) and lista_inv.strip()
    if tenia_lista:
        c.execute("UPDATE reservas SET cantidad=%s, lista_invitados=NULL, lista_subida_en=NULL WHERE id=%s",
                  (nueva_cantidad, reserva_id))
    else:
        c.execute("UPDATE reservas SET cantidad=%s WHERE id=%s", (nueva_cantidad, reserva_id))
    conn.commit()
    conn.close()
    msg = f"Cantidad actualizada a {nueva_cantidad}."
    if tenia_lista:
        msg += " La lista de invitados se borró porque ya no coincide — deben subirla de nuevo."
    return True, msg, bool(tenia_lista)

def borrar_reserva(reserva_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM reservas WHERE id=%s", (reserva_id,))
    conn.commit()
    conn.close()

def guardar_lista_invitados(reserva_id, df_inv):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE reservas SET lista_invitados=%s, lista_subida_en=%s WHERE id=%s",
              (json.dumps(df_inv.to_dict("records"), ensure_ascii=False),
               ahora_cdmx(), reserva_id))
    conn.commit()
    conn.close()

def obtener_invitados_consolidados(evento_id=None):
    df_res = get_reservas(evento_id=evento_id, estado="aprobada") if evento_id \
        else get_reservas(estado="aprobada")
    filas, faltantes = [], 0
    for _, row in df_res.iterrows():
        raw = row.get("lista_invitados")
        ev = get_evento(int(row["evento_id"]))
        if isinstance(raw, str) and raw.strip():
            for inv in json.loads(raw):
                fila = {"Evento": ev.get("nombre",""), "Tipo": row["tipo_boleto_nombre"],
                        "Código": row["codigo"]}
                fila.update(inv)
                filas.append(fila)
        else:
            faltantes += 1
    return pd.DataFrame(filas), faltantes

def leer_respaldo():
    """Exporta todos los datos como JSON (en lugar de .db ya que usamos PostgreSQL)."""
    tablas = {}
    conn = get_conn()
    for tabla in ["configuracion","eventos","tipos_boleto","reservas"]:
        df = pd.read_sql_query(f"SELECT * FROM {tabla}", conn)
        tablas[tabla] = df.to_dict("records")
    conn.close()
    return json.dumps(tablas, ensure_ascii=False, default=str).encode("utf-8")


# ════════════════════════════════════════════
# EXCEL DE INVITADOS
# ════════════════════════════════════════════

def generar_plantilla_excel(cantidad, tipo_nombre):
    from openpyxl import Workbook
    columnas = get_columnas_invitados()
    wb = Workbook()
    ws = wb.active
    ws.title = "Invitados"
    encabezados = ["#", COLUMNA_TIPO] + [c["nombre"] for c in columnas] + [COLUMNA_NB]
    ws.append(encabezados)
    for i in range(1, cantidad + 1):
        ws.append([i, tipo_nombre] + ["" for _ in columnas] + [1])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()

def df_a_excel_bytes(df):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False)
    buf.seek(0)
    return buf.getvalue()

def validar_lista_invitados(archivo, cantidad_esperada):
    try:
        df = pd.read_excel(archivo, engine="openpyxl")
    except Exception as e:
        return None, f"No pude leer el archivo: {e}"
    df.columns = [str(c).strip() for c in df.columns]
    if COLUMNA_NB not in df.columns:
        return None, f"Falta la columna '{COLUMNA_NB}'. Usa la plantilla descargada."
    columnas = get_columnas_invitados()
    requeridas = [c["nombre"] for c in columnas if c.get("requerido")]
    faltantes = [c for c in requeridas if c not in df.columns]
    if faltantes:
        return None, f"Faltan las columnas: {', '.join(faltantes)}."
    df = df.dropna(how="all").reset_index(drop=True)
    cantidades = pd.to_numeric(df[COLUMNA_NB], errors="coerce")
    if cantidades.isna().any():
        return None, f"La columna '{COLUMNA_NB}' tiene valores inválidos."
    if (cantidades <= 0).any():
        return None, f"La columna '{COLUMNA_NB}' debe ser mayor a 0 en todas las filas."
    suma = int(cantidades.sum())
    if suma != cantidad_esperada:
        return None, (f"Tu reserva es de {cantidad_esperada} boleto(s), "
                      f"pero la suma de '{COLUMNA_NB}' da {suma}.")
    for col in requeridas:
        vacios = df[col].isna() | (df[col].astype(str).str.strip() == "")
        if vacios.any():
            filas = (vacios[vacios].index + 2).tolist()
            return None, f"Falta '{col}' en la(s) fila(s) {filas}."
    return df, None


# ════════════════════════════════════════════
# CORREO
# ════════════════════════════════════════════

def _enviar(destinatario, asunto, cuerpo_html):
    try:
        rem = st.secrets["email"]["remitente"]
        pwd = st.secrets["email"]["password"]
    except Exception:
        return False, "Credenciales de correo no configuradas (secrets)."
    msg = MIMEText(cuerpo_html, "html", "utf-8")
    msg["Subject"] = asunto
    msg["From"]    = rem
    msg["To"]      = destinatario
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(rem, pwd)
            srv.sendmail(rem, [destinatario], msg.as_string())
        return True, "Enviado."
    except Exception as e:
        return False, str(e)

def _bloque_contacto():
    return get_pie_contacto().replace("\n", "<br>")

def _link_app():
    url = get_app_url()
    return f'Sigue tu reserva <a href="{url}">aquí</a>.<br>' if url else ""

def correo_confirmacion(dest, nombre, codigo, evento_nombre, tipo, cantidad, desglose=None):
    desglose_html = ""
    if desglose:
        filas = "".join(
            f"<tr>{''.join(f'<td style=padding:4px 8px>{v}</td>' for v in g.values())}</tr>"
            for g in desglose
        )
        enc = "".join(f"<th style=padding:4px 8px;text-align:left>{k}</th>" for k in desglose[0].keys())
        desglose_html = (f"<br><b>Desglose:</b><br>"
                         f"<table border=1 cellspacing=0 style=border-collapse:collapse>"
                         f"<tr>{enc}</tr>{filas}</table><br>")
    cuerpo = (
        f"Hola <b>{nombre}</b>,<br><br>"
        f"Recibimos tu solicitud de boletos para <b>{evento_nombre}</b>.<br><br>"
        f"<b>Código de reserva:</b> {codigo}<br>"
        f"<b>Tipo de boleto:</b> {tipo}<br>"
        f"<b>Cantidad:</b> {cantidad}<br>"
        f"{desglose_html}"
        f"Guarda este código — lo necesitarás para consultar tu reserva.<br><br>"
        f"{_link_app()}<br>{_bloque_contacto()}<br><br>"
        f"<i>Correo automático, por favor no respondas.</i>"
    )
    return _enviar(dest, f"Confirmación de tu solicitud — código {codigo}", cuerpo)

def correo_resultado(dest, nombre, codigo, evento_nombre, tipo, cantidad, estado, motivo=None):
    if estado == "aprobada":
        asunto = f"¡Tu solicitud fue aprobada! — {codigo}"
        cuerpo = (
            f"Hola <b>{nombre}</b>,<br><br>¡Tu solicitud fue <b>APROBADA</b>!<br><br>"
            f"<b>Código:</b> {codigo}<br><b>Evento:</b> {evento_nombre}<br>"
            f"<b>Tipo:</b> {tipo}<br><b>Cantidad:</b> {cantidad}<br><br>"
            f"Siguiente paso: entra a la app y sube la lista de tus invitados.<br><br>"
            f"{_link_app()}<br>{_bloque_contacto()}<br><br>"
            f"<i>Correo automático, por favor no respondas.</i>"
        )
    else:
        motivo_txt = f"<br><b>Motivo:</b> {motivo}" if motivo else ""
        asunto = f"Tu solicitud fue rechazada — {codigo}"
        cuerpo = (
            f"Hola <b>{nombre}</b>,<br><br>Tu solicitud fue <b>RECHAZADA</b>.<br><br>"
            f"<b>Código:</b> {codigo}<br><b>Evento:</b> {evento_nombre}<br>"
            f"<b>Tipo:</b> {tipo}<br><b>Cantidad:</b> {cantidad}{motivo_txt}<br><br>"
            f"{_bloque_contacto()}<br><br>"
            f"<i>Correo automático, por favor no respondas.</i>"
        )
    return _enviar(dest, asunto, cuerpo)

def correo_notificacion_admin(codigo, nombre, correo_sol, evento_nombre, tipo, cantidad):
    destinatarios = get_correos_notificacion()
    if not destinatarios:
        return
    cuerpo = (
        f"Nueva solicitud pendiente de revisión.<br><br>"
        f"<b>Código:</b> {codigo}<br><b>Solicitante:</b> {nombre} ({correo_sol})<br>"
        f"<b>Evento:</b> {evento_nombre}<br><b>Tipo:</b> {tipo}<br>"
        f"<b>Cantidad:</b> {cantidad}<br><br>"
        f"Entra a Administración → Solicitudes pendientes para revisarla."
    )
    try:
        rem = st.secrets["email"]["remitente"]
        pwd = st.secrets["email"]["password"]
        msg = MIMEText(cuerpo, "html", "utf-8")
        msg["Subject"] = f"Nueva solicitud — {codigo}"
        msg["From"]    = rem
        msg["To"]      = ", ".join(destinatarios)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(rem, pwd)
            srv.sendmail(rem, destinatarios, msg.as_string())
    except Exception:
        pass

def correo_recordatorio(dest, nombre, codigo, evento_nombre, tipo, cantidad):
    cuerpo = (
        f"Hola <b>{nombre}</b>,<br><br>"
        f"Tu solicitud para <b>{evento_nombre}</b> fue aprobada, "
        f"pero aún no hemos recibido tu lista de invitados.<br><br>"
        f"<b>Código:</b> {codigo}<br><b>Tipo:</b> {tipo}<br><b>Cantidad:</b> {cantidad}<br><br>"
        f"Por favor entra a la app y sube tu lista a la brevedad.<br><br>"
        f"{_link_app()}<br>{_bloque_contacto()}<br><br>"
        f"<i>Correo automático, por favor no respondas.</i>"
    )
    return _enviar(dest, f"Recordatorio: falta tu lista de invitados — {codigo}", cuerpo)

def correo_cantidad_modificada(dest, nombre, codigo, evento_nombre, tipo, nueva_cantidad, se_borro_lista):
    extra = ("<br><b>Nota:</b> La lista de invitados fue eliminada porque ya no coincide "
             "con la nueva cantidad. Vuelve a subirla.<br>" if se_borro_lista else "")
    cuerpo = (
        f"Hola <b>{nombre}</b>,<br><br>El equipo actualizó tu reserva.<br><br>"
        f"<b>Código:</b> {codigo}<br><b>Evento:</b> {evento_nombre}<br>"
        f"<b>Tipo:</b> {tipo}<br><b>Nueva cantidad:</b> {nueva_cantidad}<br>"
        f"{extra}<br>{_link_app()}<br>{_bloque_contacto()}<br><br>"
        f"<i>Correo automático, por favor no respondas.</i>"
    )
    return _enviar(dest, f"Tu reserva fue actualizada — {codigo}", cuerpo)


# ════════════════════════════════════════════
# AUTH
# ════════════════════════════════════════════

def verificar_admin():
    if "is_admin" not in st.session_state:
        st.session_state.is_admin = False
    if not st.session_state.is_admin:
        st.title("🔒 Acceso de administrador")
        pwd = st.text_input("Contraseña", type="password", key="admin_pwd_input")
        if st.button("Entrar"):
            if pwd == get_rule("admin_password", "admin123"):
                st.session_state.is_admin = True
                st.rerun()
            else:
                st.error("Contraseña incorrecta.")
        st.stop()


# ════════════════════════════════════════════
# INIT Y NAVEGACIÓN
# ════════════════════════════════════════════

init_db()

st.sidebar.title("🎟️ Eventos & Boletos")
pagina = st.sidebar.radio("Ir a:", ["Eventos", "Mi reserva", "Dashboard 🔒", "Administración"])


# ════════════════════════════════════════════
# PÁGINA: EVENTOS
# ════════════════════════════════════════════

if pagina == "Eventos":
    if "evento_reserva_id" not in st.session_state:
        st.session_state.evento_reserva_id = None

    if st.session_state.evento_reserva_id is not None:
        ev = get_evento(st.session_state.evento_reserva_id)
        if not ev:
            st.error("Evento no encontrado.")
            st.session_state.evento_reserva_id = None
            st.rerun()

        if st.button("← Volver a eventos"):
            st.session_state.evento_reserva_id = None
            st.session_state.desglose_grupos = []
            st.rerun()

        st.title(f"🎟️ {ev['nombre']}")
        col_info, col_img = st.columns([2, 1])
        with col_info:
            st.markdown(f"📅 **{fecha_bonita(ev['fecha'])}**" +
                        (f" · {ev['hora']}" if ev.get('hora') else ""))
            if ev.get("venue"):
                st.markdown(f"📍 {ev['venue']}")
            if ev.get("descripcion"):
                st.markdown(ev["descripcion"])
        with col_img:
            if ev.get("imagen_data"):
                try:
                    st.image(ev["imagen_data"], use_container_width=True)
                except Exception:
                    pass

        tipos_df = get_tipos_boleto(ev["id"])
        if tipos_df.empty:
            st.warning("Este evento no tiene tipos de boleto configurados.")
            st.stop()

        st.subheader("Elige tu tipo de boleto")
        tipo_opciones = []
        for _, t in tipos_df.iterrows():
            disp = disponible_tipo(int(t["id"]), int(t["capacidad"]))
            label = f"{t['nombre']} — {disp} disponible(s) de {t['capacidad']}"
            tipo_opciones.append((int(t["id"]), label, disp, dict(t)))

        tipo_sel_idx = st.radio("Tipo", range(len(tipo_opciones)),
                                format_func=lambda i: tipo_opciones[i][1],
                                key="tipo_boleto_radio")
        tipo_id_sel, _, disp_sel, tipo_row_sel = tipo_opciones[tipo_sel_idx]
        max_sol = max_solicitud_tipo(tipo_id_sel, tipo_row_sel)
        tope = min(disp_sel, max_sol)

        ev_fecha = date.fromisoformat(str(ev["fecha"]))
        hoy = hoy_cdmx()
        max_dias = ev.get("max_dias_anticipacion") or int(get_rule("max_dias_anticipacion", 90))
        min_dias = ev.get("min_dias_anticipacion") or int(get_rule("min_dias_anticipacion", 0))
        dias_al_evento = (ev_fecha - hoy).days

        if dias_al_evento < 0:
            st.error("Este evento ya pasó.")
            st.stop()
        if dias_al_evento < min_dias:
            st.error(f"Las reservas para este evento ya cerraron ({min_dias} día(s) antes).")
            st.stop()
        if dias_al_evento > max_dias:
            st.info(f"Las reservas abren {max_dias} días antes del evento. "
                    f"Vuelve el {fecha_bonita(ev_fecha - timedelta(days=max_dias))}.")
            st.stop()

        st.divider()
        st.subheader("Tu solicitud")

        if disp_sel == 0:
            st.error(f"El tipo **{tipo_row_sel['nombre']}** está agotado.")
        else:
            st.info(f"Disponibles: **{disp_sel}** · Máximo por solicitud: **{max_sol}**")

            st.markdown("**Tus datos**")
            c1, c2 = st.columns(2)
            with c1:
                sol_nombre = st.text_input("Tu nombre completo")
            with c2:
                sol_correo = st.text_input("Tu correo electrónico")
            sol_area = st.selectbox("Tu área", get_areas(), key="sol_area_sel")

            cantidad = st.number_input("Cantidad de boletos", min_value=1, max_value=tope, value=1, step=1)

            campos_desglose = get_campos_desglose()
            ctx = f"{tipo_id_sel}|{cantidad}"
            if st.session_state.get("desglose_ctx") != ctx:
                st.session_state.desglose_ctx = ctx
                st.session_state.desglose_grupos = []

            asignado = sum(g["Boletos"] for g in st.session_state.get("desglose_grupos", []))
            faltan   = cantidad - asignado

            st.divider()
            st.markdown("**¿Para quién son estos boletos?**")
            st.caption(f"Llevas asignados **{asignado}** de {cantidad}. Faltan **{faltan}**.")

            grupos = st.session_state.get("desglose_grupos", [])
            if grupos:
                st.dataframe(pd.DataFrame(grupos))
                if st.button("↩️ Quitar último grupo"):
                    st.session_state.desglose_grupos.pop()
                    st.rerun()

            if faltan > 0:
                with st.form("form_desglose", clear_on_submit=True):
                    vals = {campo: st.text_input(campo) for campo in campos_desglose}
                    bols = st.number_input("Boletos para este grupo", min_value=1, max_value=faltan, value=1)
                    if st.form_submit_button("Agregar grupo"):
                        if any(not v.strip() for v in vals.values()):
                            st.error("Completa todos los campos.")
                        else:
                            nuevo = {k: v.strip() for k, v in vals.items()}
                            nuevo["Boletos"] = int(bols)
                            st.session_state.desglose_grupos.append(nuevo)
                            st.rerun()
            else:
                st.success("✅ Desglose completo. Puedes enviar tu solicitud.")
                comentario = st.text_input("Nota (opcional)")

                if st.button("Enviar solicitud", type="primary"):
                    if not sol_nombre.strip():
                        st.error("Escribe tu nombre completo.")
                    elif "@" not in sol_correo or "." not in sol_correo:
                        st.error("Escribe un correo electrónico válido.")
                    else:
                        codigo = add_reserva(
                            ev["id"], tipo_id_sel, tipo_row_sel["nombre"],
                            cantidad, comentario,
                            sol_nombre.strip(), sol_correo.strip(),
                            st.session_state.desglose_grupos, area=sol_area
                        )
                        enviado, _ = correo_confirmacion(
                            sol_correo.strip(), sol_nombre.strip(), codigo,
                            ev["nombre"], tipo_row_sel["nombre"], cantidad,
                            st.session_state.desglose_grupos
                        )
                        correo_notificacion_admin(codigo, sol_nombre.strip(), sol_correo.strip(),
                                                  ev["nombre"], tipo_row_sel["nombre"], cantidad)
                        st.success(f"✅ ¡Solicitud enviada! Tu código es **{codigo}**. Guárdalo.")
                        if enviado:
                            st.caption(f"📧 Confirmación enviada a {sol_correo}.")
                        st.session_state.desglose_grupos = []
                        st.session_state.desglose_ctx = None
    else:
        st.title("🎟️ Eventos disponibles")
        eventos_df = get_eventos(solo_publicados=True)
        if eventos_df.empty:
            st.info("No hay eventos publicados en este momento.")
        else:
            ev_list = eventos_df.to_dict("records")
            for fila_inicio in range(0, len(ev_list), 3):
                cols = st.columns(3)
                for col_idx, ev in enumerate(ev_list[fila_inicio:fila_inicio+3]):
                    with cols[col_idx]:
                        with st.container(border=True):
                            if ev.get("imagen_data"):
                                try:
                                    st.image(ev["imagen_data"], use_container_width=True)
                                except Exception:
                                    pass
                            st.markdown(f"### {ev['nombre']}")
                            st.markdown(f"📅 **{fecha_bonita(ev['fecha'])}**" +
                                        (f" · {ev['hora']}" if ev.get('hora') else ""))
                            if ev.get("venue"):
                                st.markdown(f"📍 {ev['venue']}")
                            tipos_ev = get_tipos_boleto(int(ev["id"]))
                            if not tipos_ev.empty:
                                for _, t in tipos_ev.iterrows():
                                    disp = disponible_tipo(int(t["id"]), int(t["capacidad"]))
                                    prop = disp / int(t["capacidad"]) if int(t["capacidad"]) > 0 else 0
                                    badge = ("🔴 Agotado" if disp == 0
                                             else f"🟡 {disp} disponibles" if prop <= 0.3
                                             else f"🟢 {disp} disponibles")
                                    st.caption(f"**{t['nombre']}**: {badge}")
                            ev_fecha = date.fromisoformat(str(ev["fecha"]))
                            dias = (ev_fecha - hoy_cdmx()).days
                            min_d = ev.get("min_dias_anticipacion") or int(get_rule("min_dias_anticipacion", 0))
                            max_d = ev.get("max_dias_anticipacion") or int(get_rule("max_dias_anticipacion", 90))
                            if dias < 0:
                                st.button("Evento pasado", disabled=True, key=f"ev_{ev['id']}")
                            elif not (min_d <= dias <= max_d):
                                st.button("Reservas cerradas", disabled=True, key=f"ev_{ev['id']}")
                            else:
                                if st.button("🎟️ Reservar", type="primary", key=f"ev_{ev['id']}"):
                                    st.session_state.evento_reserva_id = int(ev["id"])
                                    st.session_state.desglose_grupos = []
                                    st.rerun()


# ════════════════════════════════════════════
# PÁGINA: MI RESERVA
# ════════════════════════════════════════════

elif pagina == "Mi reserva":
    st.title("🔎 Mi reserva")
    codigo_input = st.text_input("Ingresa tu código de reserva").strip().upper()
    if st.button("Buscar"):
        st.session_state.codigo_consulta = codigo_input
    codigo_actual = st.session_state.get("codigo_consulta", "")
    if codigo_actual:
        reserva = get_reserva_by_codigo(codigo_actual)
        if not reserva:
            st.error("No encontramos ninguna reserva con ese código.")
        else:
            ev = get_evento(int(reserva["evento_id"]))
            st.subheader(f"Reserva {reserva['codigo']}")
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("Evento", ev.get("nombre","—"))
            c2.metric("Tipo", reserva["tipo_boleto_nombre"])
            c3.metric("Boletos", reserva["cantidad"])
            c4.metric("Fecha evento", fecha_bonita(ev.get("fecha","")))
            st.caption(f"Solicitado por: {reserva.get('solicitante_nombre','—')} · "
                       f"{reserva.get('solicitante_correo','—')}"
                       + (f" · **Área:** {reserva['area']}" if reserva.get('area') else ""))

            estado = reserva["estado"]
            if estado == "pendiente":
                st.warning("⏳ Tu solicitud está **pendiente de revisión**.")
            elif estado == "rechazada":
                st.error("❌ Tu solicitud fue **rechazada**.")
                if reserva.get("motivo_rechazo"):
                    st.caption(f"Motivo: {reserva['motivo_rechazo']}")
            elif estado == "aprobada":
                st.success("✅ ¡Tu solicitud fue **aprobada**!")
                raw_lista = reserva.get("lista_invitados")
                ya_tiene  = isinstance(raw_lista, str) and raw_lista.strip()
                if ya_tiene:
                    st.info("Ya recibimos tu lista. Puedes corregirla volviendo a subir el archivo.")
                    st.dataframe(pd.DataFrame(json.loads(raw_lista)))
                else:
                    st.markdown("**Siguiente paso: sube la lista de tus invitados**")
                st.write("1️⃣ Descarga la plantilla y llénala:")
                st.caption(f"La suma de '{COLUMNA_NB}' debe dar {int(reserva['cantidad'])}.")
                st.download_button(
                    "⬇️ Descargar plantilla",
                    generar_plantilla_excel(int(reserva["cantidad"]), reserva["tipo_boleto_nombre"]),
                    file_name=f"invitados_{reserva['codigo']}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                st.write("2️⃣ Sube el archivo lleno:")
                archivo = st.file_uploader("Archivo .xlsx", type=["xlsx"], key=f"ul_{reserva['codigo']}")
                if archivo:
                    df_val, err = validar_lista_invitados(archivo, int(reserva["cantidad"]))
                    if err:
                        st.error(err)
                    else:
                        cols_g = [COLUMNA_TIPO] + \
                                 [c["nombre"] for c in get_columnas_invitados() if c["nombre"] in df_val.columns] + \
                                 [COLUMNA_NB]
                        guardar_lista_invitados(int(reserva["id"]),
                                               df_val[[c for c in cols_g if c in df_val.columns]])
                        st.success("¡Lista recibida! Pronto te contactarán para coordinar la entrega. 🎉")
                        st.rerun()


# ════════════════════════════════════════════
# DASHBOARD
# ════════════════════════════════════════════

elif pagina == "Dashboard 🔒":
    verificar_admin()
    st.title("📊 Dashboard")
    eventos_df = get_eventos()
    if eventos_df.empty:
        st.info("No hay eventos creados.")
        st.stop()

    todas = get_reservas()
    total_sol = len(todas)
    aprobadas = int((todas["estado"]=="aprobada").sum())  if not todas.empty else 0
    pendientes= int((todas["estado"]=="pendiente").sum()) if not todas.empty else 0
    rechazadas= int((todas["estado"]=="rechazada").sum()) if not todas.empty else 0
    m1,m2,m3,m4 = st.columns(4)
    m1.metric("Total solicitudes", total_sol)
    m2.metric("Aprobadas", aprobadas)
    m3.metric("Pendientes", pendientes)
    m4.metric("Rechazadas", rechazadas)

    st.divider()
    st.subheader("Por evento")
    filas = []
    for _, ev in eventos_df.iterrows():
        res_ev  = get_reservas(evento_id=int(ev["id"]))
        tipos_ev = get_tipos_boleto(int(ev["id"]))
        cap_total = int(tipos_ev["capacidad"].sum()) if not tipos_ev.empty else 0
        apr_ev = int(res_ev[res_ev["estado"]=="aprobada"]["cantidad"].sum()) if not res_ev.empty else 0
        pen_ev = int(res_ev[res_ev["estado"]=="pendiente"]["cantidad"].sum()) if not res_ev.empty else 0
        filas.append({"Evento": ev["nombre"], "Fecha": fecha_bonita(ev["fecha"]),
                      "Publicado": "✅" if ev["publicado"] else "❌",
                      "Capacidad": cap_total, "Aprobados": apr_ev,
                      "Pendientes": pen_ev, "Disponibles": cap_total - apr_ev})
    df_fig = pd.DataFrame(filas)
    st.dataframe(df_fig)
    df_bar = df_fig[df_fig["Aprobados"] > 0]
    if not df_bar.empty:
        fig = px.bar(df_bar, x="Evento", y=["Aprobados","Disponibles"],
                     barmode="stack", title="Boletos aprobados vs disponibles")
        st.plotly_chart(fig)

    st.divider()
    st.subheader("Detalle por evento")
    ev_sel_id = st.selectbox(
        "Evento", eventos_df["id"].tolist(),
        format_func=lambda x: eventos_df.loc[eventos_df["id"]==x,"nombre"].iloc[0]
    )
    res_sel = get_reservas(evento_id=ev_sel_id)
    if res_sel.empty:
        st.info("Sin reservas.")
    else:
        cols_m = ["codigo","tipo_boleto_nombre","cantidad","estado","area",
                  "solicitante_nombre","solicitante_correo","creado_en"]
        st.dataframe(res_sel[[c for c in cols_m if c in res_sel.columns]].rename(columns={
            "codigo":"Código","tipo_boleto_nombre":"Tipo","cantidad":"Boletos",
            "estado":"Estado","area":"Área","solicitante_nombre":"Solicitante",
            "solicitante_correo":"Correo","creado_en":"Fecha"
        }))
        st.subheader("Excel consolidado de invitados")
        df_inv_c, falt = obtener_invitados_consolidados(evento_id=ev_sel_id)
        if falt:
            st.warning(f"{falt} reserva(s) aprobada(s) sin lista todavía.")
        if not df_inv_c.empty:
            st.dataframe(df_inv_c)
            st.download_button("⬇️ Descargar Excel", df_a_excel_bytes(df_inv_c),
                               file_name=f"invitados_{ev_sel_id}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ════════════════════════════════════════════
# ADMINISTRACIÓN
# ════════════════════════════════════════════

elif pagina == "Administración":
    verificar_admin()
    st.title("⚙️ Administración")

    (tab_sol, tab_eventos, tab_tipos, tab_reglas,
     tab_formularios, tab_reservas, tab_respaldo, tab_seguridad) = st.tabs([
        "Solicitudes pendientes","Eventos","Tipos de boleto",
        "Reglas","Formularios","Reservas","Respaldo","Seguridad"
    ])

    # ── Solicitudes pendientes ────────────────
    with tab_sol:
        st.subheader("Solicitudes pendientes")
        pend = get_reservas(estado="pendiente")
        if pend.empty:
            st.info("No hay solicitudes pendientes. 🎉")
        else:
            for _, row in pend.iterrows():
                ev  = get_evento(int(row["evento_id"]))
                tid = int(row["tipo_boleto_id"])
                tipos_ev = get_tipos_boleto(int(row["evento_id"]))
                cap_tipo = 0
                if not tipos_ev.empty and tid in tipos_ev["id"].values:
                    cap_tipo = int(tipos_ev.loc[tipos_ev["id"]==tid,"capacidad"].iloc[0])
                apr_tipo = reservado_aprobado_tipo(tid)
                disp = max(0, cap_tipo - apr_tipo)
                with st.container(border=True):
                    c1,c2,c3,c4 = st.columns([2,2,2,3])
                    c1.write(f"**{row['codigo']}**")
                    c2.write(f"**{ev.get('nombre','?')}**")
                    c3.write(f"**{row['tipo_boleto_nombre']}** · {row['cantidad']} bol.")
                    c4.write(f"Disponibles: {disp} de {cap_tipo}")
                    st.caption(f"Solicitante: {row.get('solicitante_nombre','—')} · "
                               f"{row.get('solicitante_correo','—')}"
                               + (f" · **Área:** {row['area']}" if row.get('area') else ""))
                    if row.get("comentario"):
                        st.caption(f"Nota: {row['comentario']}")
                    desglose_raw = row.get("desglose_inicial")
                    if isinstance(desglose_raw, str) and desglose_raw.strip():
                        try:
                            des = json.loads(desglose_raw)
                            if des:
                                st.dataframe(pd.DataFrame(des))
                        except Exception:
                            pass
                    ca,cb = st.columns(2)
                    with ca:
                        if st.button("✅ Aprobar", key=f"apr_{row['id']}"):
                            ok, msg = aprobar_reserva(int(row["id"]))
                            if ok:
                                st.success(msg)
                                if row.get("solicitante_correo"):
                                    correo_resultado(row["solicitante_correo"],
                                        row.get("solicitante_nombre",""), row["codigo"],
                                        ev.get("nombre",""), row["tipo_boleto_nombre"],
                                        row["cantidad"], "aprobada")
                            else:
                                st.error(msg)
                            st.rerun()
                    with cb:
                        motivo = st.text_input("Motivo de rechazo", key=f"mot_{row['id']}")
                        if st.button("❌ Rechazar", key=f"rec_{row['id']}"):
                            rechazar_reserva(int(row["id"]), motivo)
                            if row.get("solicitante_correo"):
                                correo_resultado(row["solicitante_correo"],
                                    row.get("solicitante_nombre",""), row["codigo"],
                                    ev.get("nombre",""), row["tipo_boleto_nombre"],
                                    row["cantidad"], "rechazada", motivo)
                            st.warning("Solicitud rechazada.")
                            st.rerun()

    # ── Eventos ───────────────────────────────
    with tab_eventos:
        st.subheader("Eventos")
        eventos_df = get_eventos()
        if not eventos_df.empty:
            st.dataframe(eventos_df[["id","nombre","fecha","hora","venue",
                                     "publicado","creado_en"]].rename(columns={
                "id":"ID","nombre":"Evento","fecha":"Fecha","hora":"Hora",
                "venue":"Venue","publicado":"Publicado","creado_en":"Creado"}))

        st.divider()
        opciones_edit = ["➕ Crear nuevo"] + (
            [f"{r['id']} — {r['nombre']}" for _, r in eventos_df.iterrows()]
            if not eventos_df.empty else [])
        edit_sel = st.selectbox("Editar evento o crear nuevo", opciones_edit, key="edit_ev_sel")
        editando = edit_sel != "➕ Crear nuevo"
        ev_edit = {}
        if editando:
            ev_id_edit = int(edit_sel.split("—")[0].strip())
            ev_edit = get_evento(ev_id_edit)

        with st.form("form_evento"):
            c1, c2 = st.columns(2)
            with c1:
                ev_nombre = st.text_input("Nombre del evento", value=ev_edit.get("nombre",""))
                ev_fecha  = st.date_input("Fecha",
                    value=date.fromisoformat(ev_edit["fecha"]) if ev_edit.get("fecha") else hoy_cdmx())
                ev_hora   = st.text_input("Hora (ej. 20:00)", value=ev_edit.get("hora",""))
                ev_venue  = st.text_input("Venue / lugar", value=ev_edit.get("venue",""))
            with c2:
                ev_desc = st.text_area("Descripción", value=ev_edit.get("descripcion",""), height=100)
                ev_pub  = st.checkbox("Publicado", value=bool(ev_edit.get("publicado", False)))
                ev_max  = st.number_input("Máx. días anticipación",
                    min_value=0, value=ev_edit.get("max_dias_anticipacion") or int(get_rule("max_dias_anticipacion",90)))
                ev_min  = st.number_input("Mín. días anticipación (cierre)",
                    min_value=0, value=ev_edit.get("min_dias_anticipacion") or int(get_rule("min_dias_anticipacion",0)))
            st.markdown("**Imagen**")
            ev_img_url  = st.text_input("URL de imagen",
                value=ev_edit.get("imagen_data","") if ev_edit.get("imagen_data","").startswith("http") else "")
            ev_img_file = st.file_uploader("O sube una imagen", type=["jpg","jpeg","png","webp"], key="ev_img")
            if st.form_submit_button("Guardar evento"):
                if not ev_nombre.strip():
                    st.error("El nombre es obligatorio.")
                else:
                    imagen_data = ev_edit.get("imagen_data","")
                    if ev_img_file:
                        b64 = base64.b64encode(ev_img_file.read()).decode()
                        ext = ev_img_file.name.rsplit(".",1)[-1].lower()
                        mime= {"jpg":"jpeg","jpeg":"jpeg","png":"png","webp":"webp"}.get(ext,"jpeg")
                        imagen_data = f"data:image/{mime};base64,{b64}"
                    elif ev_img_url.strip():
                        imagen_data = ev_img_url.strip()
                    if editando:
                        actualizar_evento(ev_id_edit, ev_nombre.strip(), ev_fecha,
                            ev_hora.strip() or None, ev_venue.strip() or None,
                            ev_desc.strip() or None, imagen_data or None,
                            ev_pub, ev_max, ev_min)
                        st.success("Evento actualizado.")
                    else:
                        crear_evento(ev_nombre.strip(), ev_fecha,
                            ev_hora.strip() or None, ev_venue.strip() or None,
                            ev_desc.strip() or None, imagen_data or None,
                            ev_pub, ev_max, ev_min)
                        st.success("Evento creado.")
                    st.rerun()

        if editando and ev_edit:
            st.divider()
            pub_act = bool(ev_edit.get("publicado", False))
            if st.button("Despublicar" if pub_act else "Publicar", key="btn_pub"):
                actualizar_evento(ev_id_edit, ev_edit["nombre"], ev_edit["fecha"],
                    ev_edit.get("hora"), ev_edit.get("venue"), ev_edit.get("descripcion"),
                    ev_edit.get("imagen_data"), not pub_act,
                    ev_edit.get("max_dias_anticipacion"), ev_edit.get("min_dias_anticipacion"))
                st.success("Actualizado.")
                st.rerun()
            st.markdown("**Eliminar evento**")
            st.warning("Borra el evento y TODAS sus reservas.")
            if st.checkbox(f"Confirmo borrar '{ev_edit['nombre']}'", key="chk_borrar_ev"):
                if st.button("🗑️ Borrar evento", type="secondary", key="btn_borrar_ev"):
                    borrar_evento(ev_id_edit)
                    st.success("Evento eliminado.")
                    st.rerun()

    # ── Tipos de boleto ───────────────────────
    with tab_tipos:
        st.subheader("Tipos de boleto por evento")
        eventos_df2 = get_eventos()
        if eventos_df2.empty:
            st.info("Crea primero un evento.")
        else:
            ev_tipos_sel = st.selectbox("Evento", eventos_df2["id"].tolist(),
                format_func=lambda x: eventos_df2.loc[eventos_df2["id"]==x,"nombre"].iloc[0],
                key="ev_tipos_sel")
            tipos_df2 = get_tipos_boleto(ev_tipos_sel)
            if not tipos_df2.empty:
                for _, t in tipos_df2.iterrows():
                    disp = disponible_tipo(int(t["id"]), int(t["capacidad"]))
                    with st.container(border=True):
                        c1,c2,c3,c4,c5 = st.columns([3,2,2,2,1])
                        c1.write(f"**{t['nombre']}**")
                        c2.write(f"Cap: {t['capacidad']}")
                        c3.write(f"Apr: {reservado_aprobado_tipo(int(t['id']))}")
                        c4.write(f"Disp: {disp}")
                        c5.write(f"Máx: {t['max_por_solicitud'] or 'global'}")
            else:
                st.info("Sin tipos de boleto todavía.")

            st.markdown("**Agregar tipo de boleto**")
            with st.form("form_tipo"):
                c1,c2,c3 = st.columns(3)
                with c1: tnombre = st.text_input("Nombre (ej. Gradas, Palco)")
                with c2: tcap = st.number_input("Capacidad", min_value=1, value=50)
                with c3: tmaxsol = st.number_input("Máx/solicitud (0=global)", min_value=0, value=0)
                if st.form_submit_button("Agregar"):
                    if tnombre.strip():
                        crear_tipo_boleto(ev_tipos_sel, tnombre.strip(), tcap, tmaxsol if tmaxsol > 0 else None)
                        st.success("Tipo agregado.")
                        st.rerun()

            if not tipos_df2.empty:
                st.markdown("**Editar / Eliminar tipo**")
                tid_edit = st.selectbox("Tipo", tipos_df2["id"].tolist(),
                    format_func=lambda x: tipos_df2.loc[tipos_df2["id"]==x,"nombre"].iloc[0],
                    key="tipo_edit")
                trow = tipos_df2.loc[tipos_df2["id"]==tid_edit].iloc[0]
                c1,c2,c3,c4 = st.columns([3,2,2,1])
                with c1: tnombre_e = st.text_input("Nombre", value=trow["nombre"], key="ten")
                with c2: tcap_e = st.number_input("Cap", min_value=1, value=int(trow["capacidad"]), key="tce")
                with c3: tmaxsol_e = st.number_input("Máx/sol", min_value=0,
                    value=int(trow["max_por_solicitud"]) if trow["max_por_solicitud"] else 0, key="tme")
                with c4:
                    st.write("")
                    if st.button("Guardar", key="btn_tguardar"):
                        actualizar_tipo_boleto(tid_edit, tnombre_e, tcap_e, tmaxsol_e if tmaxsol_e > 0 else None)
                        st.success("Actualizado.")
                        st.rerun()
                if st.button("🗑️ Eliminar este tipo", type="secondary", key="btn_tborrar"):
                    borrar_tipo_boleto(int(tid_edit))
                    st.success("Tipo eliminado.")
                    st.rerun()

    # ── Reglas ────────────────────────────────
    with tab_reglas:
        st.subheader("Reglas globales")
        max_g = st.number_input("Máximo global de boletos por solicitud",
            min_value=1, value=int(get_rule("max_boletos_global",10)))
        max_a = st.number_input("Máx. días de anticipación (global)",
            min_value=0, value=int(get_rule("max_dias_anticipacion",90)))
        min_a = st.number_input("Mín. días de anticipación — cierre (global)",
            min_value=0, value=int(get_rule("min_dias_anticipacion",0)))
        if st.button("Guardar reglas", type="primary", key="btn_reglas"):
            set_rule("max_boletos_global", max_g)
            set_rule("max_dias_anticipacion", max_a)
            set_rule("min_dias_anticipacion", min_a)
            st.success("Reglas actualizadas.")

        st.divider()
        st.subheader("Áreas")
        areas_act = get_areas()
        texto_areas = st.text_area("Una área por línea", value="\n".join(areas_act), height=200, key="txt_areas")
        if st.button("Guardar áreas", key="btn_areas"):
            nuevas = [a.strip() for a in texto_areas.split("\n") if a.strip()]
            if nuevas:
                set_areas(nuevas)
                st.success(f"{len(nuevas)} área(s) guardadas.")
            else:
                st.error("Debe haber al menos un área.")

        st.divider()
        st.subheader("Correos de notificación de nuevas solicitudes")
        correos_act = get_correos_notificacion()
        txt_correos = st.text_area("Un correo por línea", value="\n".join(correos_act), height=100, key="txt_correos_notif")
        if st.button("Guardar correos", key="btn_correos_notif"):
            nuevos = [c.strip() for c in txt_correos.split("\n") if c.strip()]
            invalidos = [c for c in nuevos if "@" not in c]
            if invalidos:
                st.error(f"Correos inválidos: {', '.join(invalidos)}")
            else:
                set_rule("correos_notificacion", json.dumps(nuevos, ensure_ascii=False))
                st.success("Guardado.")

        st.divider()
        st.subheader("Pie de correos (contactos)")
        nuevo_pie = st.text_area("Texto del pie", value=get_pie_contacto(), height=100, key="txt_pie")
        if st.button("Guardar pie", key="btn_pie"):
            set_rule("pie_contacto", nuevo_pie)
            st.success("Guardado.")

        st.divider()
        st.subheader("URL de la app")
        nuevo_url = st.text_input("URL de la app (para enlaces en correos)", value=get_app_url(), key="txt_url")
        if st.button("Guardar URL", key="btn_url"):
            set_rule("app_url", nuevo_url.strip())
            st.success("Guardado.")

    # ── Formularios ───────────────────────────
    with tab_formularios:
        st.subheader("Desglose inicial")
        campos_des = get_campos_desglose()
        if campos_des:
            st.dataframe(pd.DataFrame({"Campo": campos_des}))
        c1,c2 = st.columns([3,1])
        with c1: nc_des = st.text_input("Nuevo campo", key="nc_des")
        with c2:
            st.write("")
            if st.button("Agregar", key="btn_add_campo"):
                nc = nc_des.strip()
                if nc and nc not in campos_des:
                    campos_des.append(nc)
                    set_campos_desglose(campos_des)
                    st.rerun()
        if campos_des:
            c1,c2 = st.columns([3,1])
            with c1: cq = st.selectbox("Campo a quitar", campos_des, key="cq_des")
            with c2:
                st.write("")
                if st.button("Quitar", type="secondary", key="btn_q_campo"):
                    if len(campos_des) > 1:
                        campos_des.remove(cq)
                        set_campos_desglose(campos_des)
                        st.rerun()

        st.divider()
        st.subheader("Columnas de la lista de invitados (post-aprobación)")
        cols_inv = get_columnas_invitados()
        st.dataframe(pd.DataFrame([{"Columna":c["nombre"],"Obligatoria":c.get("requerido",False),
                                     "Tipo":"Combo" if c.get("opciones") else "Texto"} for c in cols_inv]))
        c1,c2 = st.columns([3,1])
        with c1: nc_inv = st.text_input("Nombre de la columna", key="nc_inv")
        with c2: req_inv = st.checkbox("Obligatoria", key="req_inv")
        opt_txt = st.text_area("Opciones combo (una por línea, vacío=texto libre)", key="opt_inv", height=60)
        if st.button("Agregar columna", key="btn_add_col_inv"):
            nc = nc_inv.strip()
            if nc and nc not in [c["nombre"] for c in cols_inv]:
                nueva = {"nombre": nc, "requerido": req_inv}
                opts = [o.strip() for o in opt_txt.split("\n") if o.strip()]
                if opts: nueva["opciones"] = opts
                cols_inv.append(nueva)
                set_columnas_invitados(cols_inv)
                st.rerun()
        if cols_inv:
            c1,c2 = st.columns([3,1])
            with c1: cq_inv = st.selectbox("Columna a quitar", [c["nombre"] for c in cols_inv], key="cq_inv")
            with c2:
                st.write("")
                if st.button("Quitar", type="secondary", key="btn_q_col_inv"):
                    rest = [c for c in cols_inv if c["nombre"] != cq_inv]
                    if rest:
                        set_columnas_invitados(rest)
                        st.rerun()

    # ── Reservas ──────────────────────────────
    with tab_reservas:
        st.subheader("Todas las reservas")
        eventos_df3 = get_eventos()
        c1,c2 = st.columns(2)
        with c1:
            ev_fil = st.selectbox("Evento", ["Todos"] + (eventos_df3["id"].tolist() if not eventos_df3.empty else []),
                format_func=lambda x: "Todos" if x=="Todos" else eventos_df3.loc[eventos_df3["id"]==x,"nombre"].iloc[0],
                key="ev_fil_res")
        with c2:
            est_fil = st.selectbox("Estado", ["Todos","pendiente","aprobada","rechazada"], key="est_fil")
        df_res = get_reservas(evento_id=None if ev_fil=="Todos" else ev_fil,
                              estado=None if est_fil=="Todos" else est_fil)

        if df_res.empty:
            st.info("Sin reservas.")
        else:
            cols_m = ["id","codigo","tipo_boleto_nombre","cantidad","estado","area",
                      "solicitante_nombre","solicitante_correo","creado_en"]
            st.dataframe(df_res[[c for c in cols_m if c in df_res.columns]].rename(columns={
                "id":"ID","codigo":"Código","tipo_boleto_nombre":"Tipo","cantidad":"Boletos",
                "estado":"Estado","area":"Área","solicitante_nombre":"Solicitante",
                "solicitante_correo":"Correo","creado_en":"Fecha"}))

            st.markdown("**Detalle / editar**")
            res_sel_id = st.selectbox("ID", df_res["id"].tolist(), key="res_det_sel")
            res_row = df_res[df_res["id"]==res_sel_id].iloc[0].to_dict()
            ev_res  = get_evento(int(res_row["evento_id"]))

            st.write(f"**Código:** {res_row['codigo']} · **Estado:** {res_row['estado']}")
            st.caption(f"Solicitante: {res_row.get('solicitante_nombre','—')} · "
                       f"{res_row.get('solicitante_correo','—')}"
                       + (f" · **Área:** {res_row['area']}" if res_row.get('area') else ""))

            desglose_raw = res_row.get("desglose_inicial")
            if isinstance(desglose_raw, str) and desglose_raw.strip():
                try:
                    des = json.loads(desglose_raw)
                    if des: st.dataframe(pd.DataFrame(des))
                except Exception: pass

            raw_lista = res_row.get("lista_invitados")
            if isinstance(raw_lista, str) and raw_lista.strip():
                inv_df = pd.DataFrame(json.loads(raw_lista))
                st.dataframe(inv_df)
                st.download_button("⬇️ Descargar lista", df_a_excel_bytes(inv_df),
                    file_name=f"invitados_{res_row['codigo']}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_{res_sel_id}")
            else:
                st.caption("Sin lista de invitados.")
                if res_row["estado"]=="aprobada" and res_row.get("solicitante_correo"):
                    if st.button("📧 Enviar recordatorio", key=f"rec_{res_sel_id}"):
                        ok,msg = correo_recordatorio(res_row["solicitante_correo"],
                            res_row.get("solicitante_nombre",""), res_row["codigo"],
                            ev_res.get("nombre",""), res_row["tipo_boleto_nombre"], res_row["cantidad"])
                        st.success("Enviado.") if ok else st.error(msg)

            st.markdown("**Modificar cantidad**")
            nueva_cant = st.number_input("Nueva cantidad", min_value=1,
                value=int(res_row["cantidad"]), key=f"nc_{res_sel_id}")
            if st.button("Actualizar cantidad", key=f"btn_ac_{res_sel_id}"):
                ok, msg, borro = editar_cantidad_reserva(int(res_sel_id), nueva_cant)
                if ok:
                    st.success(msg)
                    if borro and res_row.get("solicitante_correo"):
                        correo_cantidad_modificada(res_row["solicitante_correo"],
                            res_row.get("solicitante_nombre",""), res_row["codigo"],
                            ev_res.get("nombre",""), res_row["tipo_boleto_nombre"], nueva_cant, True)
                    st.rerun()
                else:
                    st.error(msg)

            st.divider()
            sin_lista = df_res[(df_res["estado"]=="aprobada") &
                ~df_res["lista_invitados"].apply(lambda v: isinstance(v,str) and bool(v.strip()))]
            if not sin_lista.empty:
                st.warning(f"{len(sin_lista)} reserva(s) aprobada(s) sin lista.")
                if st.button(f"📧 Recordatorio masivo ({len(sin_lista)})", key="btn_rec_mas"):
                    env_r = 0
                    for _, r in sin_lista.iterrows():
                        if r.get("solicitante_correo"):
                            ev_r = get_evento(int(r["evento_id"]))
                            ok,_ = correo_recordatorio(r["solicitante_correo"],
                                r.get("solicitante_nombre",""), r["codigo"],
                                ev_r.get("nombre",""), r["tipo_boleto_nombre"], r["cantidad"])
                            if ok: env_r += 1
                    st.success(f"Recordatorios enviados: {env_r}.")

            if st.button("🗑️ Cancelar esta reserva", type="secondary", key=f"btn_br_{res_sel_id}"):
                borrar_reserva(int(res_sel_id))
                st.success("Reserva eliminada.")
                st.rerun()

        st.divider()
        st.subheader("🔧 Crear reserva manualmente")
        st.caption("Para crear sin pasar por el formulario público o recuperar una reserva perdida.")
        eventos_df4 = get_eventos()
        if not eventos_df4.empty:
            with st.form("form_res_manual"):
                r1,r2 = st.columns(2)
                with r1:
                    ev_m = st.selectbox("Evento", eventos_df4["id"].tolist(),
                        format_func=lambda x: eventos_df4.loc[eventos_df4["id"]==x,"nombre"].iloc[0],
                        key="ev_m")
                    tipos_m = get_tipos_boleto(ev_m)
                    tipo_m_id, tipo_m_nombre = None, ""
                    if not tipos_m.empty:
                        tipo_m_id = st.selectbox("Tipo", tipos_m["id"].tolist(),
                            format_func=lambda x: tipos_m.loc[tipos_m["id"]==x,"nombre"].iloc[0],
                            key="tipo_m")
                        tipo_m_nombre = tipos_m.loc[tipos_m["id"]==tipo_m_id,"nombre"].iloc[0]
                    man_nombre = st.text_input("Nombre solicitante", key="man_nombre")
                    man_correo = st.text_input("Correo solicitante", key="man_correo")
                    man_area   = st.selectbox("Área", get_areas(), key="man_area")
                with r2:
                    man_cant    = st.number_input("Cantidad", min_value=1, value=1, key="man_cant")
                    man_estado  = st.selectbox("Estado", ["pendiente","aprobada","rechazada"], key="man_estado")
                    man_codigo  = st.text_input("Código (vacío=automático)", key="man_codigo")
                    man_nota    = st.text_input("Nota (opcional)", key="man_nota")
                if st.form_submit_button("Crear reserva"):
                    if not man_nombre.strip() or not man_correo.strip():
                        st.error("Nombre y correo obligatorios.")
                    elif tipo_m_id is None:
                        st.error("Selecciona un tipo de boleto.")
                    else:
                        ok, msg = crear_reserva_manual(ev_m, tipo_m_id, tipo_m_nombre,
                            man_cant, man_nota, man_nombre.strip(), man_correo.strip(),
                            man_estado, man_codigo, desglose_inicial=[], area=man_area)
                        if ok: st.success(msg)
                        else:  st.error(msg)

    # ── Respaldo ──────────────────────────────
    with tab_respaldo:
        st.subheader("Respaldo de datos")
        st.caption("Como ahora usamos PostgreSQL/Supabase, el respaldo se exporta como JSON (incluye todos los datos: eventos, tipos, reservas y configuración).")
        try:
            st.download_button(
                "⬇️ Descargar respaldo (.json)",
                leer_respaldo(),
                file_name=f"respaldo_eventos_{hoy_cdmx().isoformat()}.json",
                mime="application/json"
            )
        except Exception as e:
            st.error(f"Error al generar respaldo: {e}")
        st.info("💡 Tus datos también están seguros en Supabase — puedes ver y descargar todo desde el dashboard de supabase.com en cualquier momento.")

    # ── Seguridad ─────────────────────────────
    with tab_seguridad:
        st.subheader("Contraseña de administrador")
        nueva_pwd = st.text_input("Nueva contraseña", type="password", key="nueva_pwd_adm")
        if st.button("Actualizar", key="btn_pwd"):
            if nueva_pwd.strip():
                set_rule("admin_password", nueva_pwd.strip())
                st.success("Contraseña actualizada.")
            else:
                st.error("No puede estar vacía.")
        st.divider()
        if st.button("Cerrar sesión de administrador", key="btn_cerrar"):
            st.session_state.is_admin = False
            st.rerun()
