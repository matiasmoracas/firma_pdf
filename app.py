import streamlit as st
from streamlit_drawable_canvas import st_canvas
from PIL import Image
import io, re
import fitz  # PyMuPDF
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import datetime
from zoneinfo import ZoneInfo  # hora de Chile

# ====== PARÁMETROS ======
PHOTO_WIDTH_PT = 400                 # ancho de la foto en el PDF (pt)
CHILE_TZ = ZoneInfo("America/Santiago")

st.set_page_config(page_title="Firmas Guías de Salida Ingefix", layout="centered")
st.title("Gestor de firmas Guías Ingefix")

# ----------------------------------------------------------------------
# ======================= RUT: helpers (NUEVO) =========================
# ----------------------------------------------------------------------
def _clean_rut(rut: str) -> str:
    """Quita todo excepto dígitos y K/k; devuelve en minúsculas."""
    return re.sub(r"[^0-9kK]", "", (rut or "")).lower()

def _calc_dv(num: str) -> str:
    """Calcula dígito verificador usando módulo 11 (pesos 2..7)."""
    s = 0
    mult = 2
    for d in reversed(num):
        s += int(d) * mult
        mult = 2 if mult == 7 else mult + 1
    r = 11 - (s % 11)
    if r == 11: return "0"
    if r == 10: return "k"
    return str(r)

def _format_miles(cuerpo: str) -> str:
    """Agrega puntos de miles al cuerpo del RUT."""
    if not cuerpo:
        return ""
    partes = []
    while len(cuerpo) > 3:
        partes.insert(0, cuerpo[-3:])
        cuerpo = cuerpo[:-3]
    if cuerpo:
        partes.insert(0, cuerpo)
    return ".".join(partes)

def format_rut(rut: str) -> str:
    """Devuelve el RUT con puntos y guion (ej: 12.345.678-9)."""
    rut = _clean_rut(rut)
    if not rut:
        return ""
    if len(rut) == 1:
        # Solo DV o un dígito aún; no formatear
        return rut
    cuerpo, dv = rut[:-1], rut[-1]
    if not cuerpo.isdigit():
        return rut  # incompleto; no formatear aún
    return f"{_format_miles(cuerpo)}-{dv}"

def validate_rut(rut: str) -> bool:
    """Valida el RUT con su DV."""
    rut = _clean_rut(rut)
    if len(rut) < 2 or not rut[:-1].isdigit():
        return False
    cuerpo, dv = rut[:-1], rut[-1]
    return _calc_dv(cuerpo) == dv

def rut_on_change():
    """Callback: toma lo escrito y lo deja formateado en vivo."""
    raw = st.session_state.get("rut_raw", "")
    formatted = format_rut(raw)
    st.session_state["rut"] = formatted
    st.session_state["rut_raw"] = formatted
# ----------------------------------------------------------------------

# Subir PDF
pdf_file = st.file_uploader("Sube La Guía de Salida", type=["pdf"])

# ========== FORMULARIO CLIENTE ==========
with st.expander("🧾 **Formulario Cliente**", expanded=True):
    nombre = st.text_input("Nombre")
    recinto = st.text_input("Recinto")
    fecha = st.date_input("Fecha", value=datetime.date.today())
    fecha_str = fecha.strftime("%d-%m-%Y")

    # -------- RUT con formateo automático (NUEVO) --------
    st.text_input("RUT", key="rut_raw", on_change=rut_on_change, placeholder="12.345.678-9")
    rut = st.session_state.get("rut", st.session_state.get("rut_raw", ""))

    # Mensaje de validación (opcional)
    if rut and not validate_rut(rut):
        st.caption("⚠️ RUT inválido (revisa dígito verificador).")

# ---------- Helper: extraer Nº de Guía del PDF ----------
def extraer_numero_guia(pdf_bytes):
    """
    Busca patrones como: 'Nº 123456', 'N°123456', 'No 123456', 'Nro 123456'.
    Devuelve el número (solo dígitos) o None.
    """
    patron = re.compile(r"(?:Nº|N°|No|N\.o|Nro\.?)\s*([0-9]{5,8})", re.IGNORECASE)
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page in doc:
            texto = page.get_text()
            m = patron.search(texto)
            if m:
                doc.close()
                return m.group(1)
        doc.close()
    except Exception:
        pass
    return None

# Guardamos bytes y detectamos Nº guía ANTES de crear el input
pdf_bytes = None
if pdf_file is not None:
    pdf_bytes = pdf_file.read()
    numero_detectado = extraer_numero_guia(pdf_bytes)
    if numero_detectado:
        st.session_state["numero_guia"] = numero_detectado

# ========== FORMULARIO CHOFER / DESPACHADOR ==========
with st.expander("🚚 **Formulario Chofer / Despachador**", expanded=True):
    observacion = st.text_area("Observación")
    iniciales_chofer = st.selectbox("Iniciales del Chofer", ["MOC", "BFS", "MFV"])
    numero_guia = st.text_input(
        "Número de la Guía",
        value=st.session_state.get("numero_guia", ""),
        key="numero_guia"
    )
    nombre_pdf = f"GS {numero_guia} {iniciales_chofer}".strip()

# ========== FOTO DEL RECINTO (SUBIDA DE ARCHIVO) ==========
with st.expander("📷 Foto del Recinto", expanded=False):
    st.markdown("Sube una foto del recinto (JPG o PNG). Se insertará en el PDF con sello de fecha/hora (zona Chile).")
    foto_file = st.file_uploader("Selecciona una imagen", type=["jpg", "jpeg", "png"], key="foto_recinto")

    # Preview (opcional) solo si se sube
    if foto_file is not None:
        st.image(foto_file, caption="Preview de la foto subida", use_container_width=True)

# ================= FUNCIÓN PARA MODIFICAR EL PDF ====================
def insertar_firma_y_texto_en_pdf(
    pdf_bytes,
    firma_img,
    nombre,
    recinto,
    fecha_str,
    rut,
    observacion,
    firma_width=120,
    foto_bytes=None,            # bytes crudos de la foto (sin compresión)
    foto_ancho_pt=PHOTO_WIDTH_PT,
    fecha_hora_foto=None        # texto del sello horario (solo sobre la foto)
):
    """
    Inserta firma, textos y opcionalmente una foto (con rótulo de fecha/hora, solo donde va la foto).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pagina = doc[-1]  # última página

    def insertar_dato_campo(etiqueta, texto, offset_x=5, offset_y=4):
        resultados = pagina.search_for(etiqueta)
        if resultados:
            box = resultados[0]
            x = box.x1 + offset_x
            y = box.y0 + offset_y
            pagina.insert_text((x, y), texto, fontsize=11, fontname="helv", fill=(0, 0, 0))

    # Campos (no incluyen hora; solo fecha del formulario)
    insertar_dato_campo("Nombre:", nombre, offset_x=15, offset_y=4)
    insertar_dato_campo("Recinto:", recinto, offset_x=15, offset_y=7)
    insertar_dato_campo("RUT:", rut, offset_x=5, offset_y=4)
    insertar_dato_campo("Fecha:", fecha_str, offset_x=20, offset_y=8)

    # Firma (del canvas)
    firma_box = pagina.search_for("Firma")
    if firma_box:
        rect = firma_box[0]
        x = rect.x0 + 10
        y = rect.y0 - 20
        img_bytes = io.BytesIO()
        firma_img.save(img_bytes, format='PNG')
        img_bytes = img_bytes.getvalue()
        image = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        w_orig, h_orig = image.size
        escala = firma_width / w_orig
        h_escala = h_orig * escala
        firma_rect = fitz.Rect(x, y, x + firma_width, y + h_escala)
        pagina.insert_image(firma_rect, stream=img_bytes)

    # Observación y ancla para foto
    y_obs_base = None
    cedible_box = pagina.search_for("CEDIBLE")
    if cedible_box and observacion.strip():
        cbox = cedible_box[0]
        page_width = pagina.rect.width
        y_obs = cbox.y1 + 10
        texto_label = "Observación:"
        ancho_label = fitz.get_text_length(texto_label, fontsize=11, fontname="helv")
        ancho_campo = 280
        alto_campo = 45
        espacio = 10
        total_ancho = ancho_label + espacio + ancho_campo
        x_inicio = (page_width - total_ancho) / 2
        pagina.insert_text((x_inicio, y_obs + 5), texto_label, fontsize=11, fontname="helv", fill=(0, 0, 0))
        textbox_rect = fitz.Rect(
            x_inicio + ancho_label + espacio, y_obs,
            x_inicio + ancho_label + espacio + ancho_campo, y_obs + alto_campo
        )
        pagina.draw_rect(textbox_rect, color=(0, 0, 0), width=0.5)
        pagina.insert_textbox(textbox_rect, observacion.strip(), fontsize=10, fontname="helv", align=0, fill=(0, 0, 0))
        y_obs_base = textbox_rect.y1

    # Foto (opcional, sin compresión) + sello horario SOLO en la zona de la foto
    if foto_bytes is not None:
        try:
            img_tmp = Image.open(io.BytesIO(foto_bytes))
            wpx, hpx = img_tmp.size
            ratio = hpx / wpx if wpx else 1
            margen = 36  # 0.5"
            page_width = pagina.rect.width
            page_height = pagina.rect.height
            ancho_pt = max(min(foto_ancho_pt, page_width - 2 * margen), 120)
            alto_pt = ancho_pt * ratio

            # Posición preferida
            if y_obs_base is not None:
                y_start = y_obs_base + 26  # espacio para el rótulo
            elif cedible_box:
                y_start = cedible_box[0].y1 + 34
            else:
                y_start = page_height * 0.55

            x_left = max((page_width - ancho_pt) / 2, margen)
            ancho_pt_real = ancho_pt
            alto_pt_real = ancho_pt_real * ratio

            # Rótulo SOLO sobre la foto
            if fecha_hora_foto:
                etiqueta = f"Foto del recinto — capturada el {fecha_hora_foto}"
                pagina.insert_text((x_left, y_start - 12), etiqueta, fontsize=10, fontname="helv", fill=(0, 0, 0))

            # Insertar imagen tal cual (sin recomprimir)
            if y_start + alto_pt_real + margen <= page_height:
                target_rect = fitz.Rect(x_left, y_start, x_left + ancho_pt_real, y_start + alto_pt_real)
                pagina.insert_image(target_rect, stream=foto_bytes)
            else:
                new_page = doc.new_page(-1)
                pw, ph = new_page.rect.width, new_page.rect.height
                x_left = max((pw - ancho_pt) / 2, margen)
                alto_pt_real = ancho_pt * ratio
                if fecha_hora_foto:
                    new_page.insert_text((x_left, margen - 12), etiqueta, fontsize=10, fontname="helv", fill=(0, 0, 0))
                target_rect = fitz.Rect(x_left, margen, x_left + ancho_pt, margen + alto_pt_real)
                new_page.insert_image(target_rect, stream=foto_bytes)
        except Exception as e:
            print("Error insertando foto en PDF:", e)

    # Salida
    output = io.BytesIO()
    doc.save(output)
    doc.close()
    output.seek(0)
    return output

def render_preview(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pagina = doc[-1]
    zoom = 4
    mat = fitz.Matrix(zoom, zoom)
    pix = pagina.get_pixmap(matrix=mat)
    img_data = pix.tobytes("png")
    doc.close()
    return img_data

def subir_a_drive(nombre_archivo, contenido_pdf):
    creds_info = st.secrets["gcp_service_account"]
    credentials = service_account.Credentials.from_service_account_info(creds_info)
    servicio = build("drive", "v3", credentials=credentials)
    file_metadata = {"name": nombre_archivo, "mimeType": "application/pdf", "parents": ["0AFh4pnUAC83dUk9PVA"]}
    contenido_pdf.seek(0)
    media = MediaIoBaseUpload(contenido_pdf, mimetype="application/pdf")
    archivo = servicio.files().create(body=file_metadata, media_body=media, fields="id", supportsAllDrives=True).execute()
    return archivo.get("id")

# ================= UI PRINCIPAL ====================
if pdf_bytes is not None:
    st.subheader("Vista previa del documento original:")
    st.image(render_preview(pdf_bytes), use_container_width=True)

    st.subheader("Dibuja tu firma aquí:")
    canvas_result = st_canvas(
        fill_color="rgba(0, 0, 0, 0)", stroke_width=2, stroke_color="black",
        background_color="#ffffff00", height=150, width=400, drawing_mode="freedraw", key="canvas"
    )

    signature_img = None
    if canvas_result.image_data is not None:
        signature_img = Image.fromarray((canvas_result.image_data).astype("uint8"))

    if st.button("Firmar Documento"):
        # Validaciones incluyendo RUT
        if signature_img is None:
            st.warning("⚠️ Dibuja tu firma primero.")
        elif not (nombre and recinto and fecha and rut and st.session_state.get("numero_guia", "")):
            st.warning("⚠️ Completa todos los campos del formulario.")
        elif not validate_rut(rut):
            st.warning("⚠️ El RUT no es válido.")
        else:
            # bytes de la foto: archivo subido (sin compresión)
            foto_bytes_raw = foto_file.getvalue() if ('foto_recinto' in st.session_state and st.session_state['foto_recinto']) else (foto_file.getvalue() if 'foto_file' in locals() and foto_file is not None else (foto_file.getvalue() if foto_file is not None else None))
            # más simple/robusto:
            foto_bytes_raw = foto_file.getvalue() if 'foto_file' in locals() and foto_file is not None else (foto_file.getvalue() if 'foto_recinto' in st.session_state and st.session_state['foto_recinto'] else (foto_file.getvalue() if foto_file is not None else None))
            # y finalmente:
            foto_bytes_raw = foto_file.getvalue() if foto_file is not None else None

            # Fecha/hora con zona de Chile (solo para el rótulo en la foto)
            fecha_hora_foto = datetime.datetime.now(tz=CHILE_TZ).strftime("%d-%m-%Y %H:%M:%S")

            # construir PDF final
            pdf_final_io = insertar_firma_y_texto_en_pdf(
                pdf_bytes=pdf_bytes, firma_img=signature_img, nombre=nombre, recinto=recinto,
                fecha_str=fecha_str, rut=rut, observacion=observacion, firma_width=120,
                foto_bytes=foto_bytes_raw, foto_ancho_pt=PHOTO_WIDTH_PT, fecha_hora_foto=fecha_hora_foto
            )

            if pdf_final_io:
                st.success("✅ Documento firmado y foto incrustada correctamente.")
                with st.spinner("Subiendo a Google Drive..."):
                    subir_a_drive(f"GS {st.session_state['numero_guia']} {iniciales_chofer}.pdf", pdf_final_io)
                st.success("Documento enviado a Google Drive con éxito")

                st.subheader("Vista previa del documento final:")
                st.image(render_preview(pdf_final_io.getvalue()), use_container_width=True)

                st.download_button(
                    label="Descargar Documento Firmado", data=pdf_final_io,
                    file_name=f"GS {st.session_state['numero_guia']} {iniciales_chofer}.pdf", mime="application/pdf"
                )

st.markdown("""
---
<center style='color: gray;'>Desarrollado por Ingefix 2025</center>
""", unsafe_allow_html=True)

