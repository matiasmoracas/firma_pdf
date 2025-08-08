import streamlit as st
from streamlit_drawable_canvas import st_canvas
from PIL import Image
import io
import fitz  # PyMuPDF
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import datetime

st.set_page_config(page_title="Firmas Guías de Salida Ingefix", layout="centered")
st.title("Gestor de firmas Guías Ingefix")

# Subir PDF
pdf_file = st.file_uploader("Sube La Guía de Salida", type=["pdf"])

# ========== FORMULARIO CLIENTE ==========
with st.expander("🧾 **Formulario Cliente**", expanded=True):
    nombre = st.text_input("Nombre")
    recinto = st.text_input("Recinto")
    fecha = st.date_input("Fecha", value=datetime.date.today())
    fecha_str = fecha.strftime("%d-%m-%Y")
    rut = st.text_input("RUT")

# ========== FORMULARIO CHOFER / DESPACHADOR ==========
with st.expander("🚚 **Formulario Chofer / Despachador**", expanded=True):
    observacion = st.text_area("Observación")
    iniciales_chofer = st.selectbox("Iniciales del Chofer", ["MOC", "BFS", "MFV"])
    numero_guia = st.text_input("Número de la Guía", "")
    nombre_pdf = f"GS {numero_guia} {iniciales_chofer}"

# ========== FOTO DEL RECINTO (OPCIONAL) ==========
with st.expander("📷 Foto del Recinto (opcional)", expanded=False):
    foto_file = st.file_uploader("Sube una foto (JPG/PNG)", type=["jpg", "jpeg", "png"], key="foto_recinto")
    calidad = st.slider("Calidad JPEG", 5, 50, 25, help="Menor = más compresión (menos KB)")
    max_lado = st.select_slider("Máx. lado (px)", options=[480, 720, 1024, 1280, 1600], value=1024)
    ancho_en_pdf = st.select_slider("Ancho de la foto en PDF (pt)", options=[240, 300, 360, 420], value=360,
                                    help="1 pt ≈ 1/72 de pulgada. 360 pt ~ 12,7 cm")
    if foto_file is not None:
        st.image(foto_file, caption="Preview (original)", use_container_width=True)

# ================= HELPER: COMPRIMIR IMAGEN ====================
def comprimir_imagen(file_bytes, max_lado=1024, calidad=25):
    """
    - Redimensiona manteniendo aspecto hasta que el lado mayor sea <= max_lado
    - Convierte a JPEG con alta compresión para pesar pocos KB
    Devuelve BytesIO (JPEG) listo para incrustar en PDF.
    """
    img = Image.open(io.BytesIO(file_bytes)).convert("RGB")

    # Redimensionar manteniendo proporción si supera el máximo
    w, h = img.size
    scale = min(max_lado / max(w, h), 1.0)
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=int(calidad), optimize=True, subsampling="4:2:0", progressive=True)
    out.seek(0)
    return out

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
    foto_jpeg_bytes=None,
    foto_ancho_pt=360,
):
    """
    Si 'foto_jpeg_bytes' viene, inserta la foto:
      - Primero intenta debajo de la sección 'Observación:' en la última página.
      - Si no hay espacio suficiente, agrega una nueva página y la centra arriba.
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

    insertar_dato_campo("Nombre:", nombre, offset_x=15, offset_y=4)
    insertar_dato_campo("Recinto:", recinto, offset_x=15, offset_y=7)
    insertar_dato_campo("RUT:", rut, offset_x=5, offset_y=4)
    insertar_dato_campo("Fecha:", fecha_str, offset_x=20, offset_y=8)

    # Firma
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

    # Observación
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
            x_inicio + ancho_label + espacio,
            y_obs,
            x_inicio + ancho_label + espacio + ancho_campo,
            y_obs + alto_campo
        )
        pagina.draw_rect(textbox_rect, color=(0, 0, 0), width=0.5)
        pagina.insert_textbox(
            textbox_rect,
            observacion.strip(),
            fontsize=10,
            fontname="helv",
            align=0,
            fill=(0, 0, 0)
        )
        y_obs_base = textbox_rect.y1  # parte baja del recuadro de observación

    # Foto (opcional) — intentamos en la última página
    if foto_jpeg_bytes is not None:
        try:
            # Calcula alto manteniendo aspecto a partir del ancho deseado (en pt)
            img_tmp = Image.open(io.BytesIO(foto_jpeg_bytes))
            wpx, hpx = img_tmp.size
            ratio = hpx / wpx if wpx else 1
            alto_pt = foto_ancho_pt * ratio

            margen = 36  # 0.5"
            page_width = pagina.rect.width
            page_height = pagina.rect.height

            # Posición preferida: debajo de observación si existe, sino debajo de 'CEDIBLE', sino centro/abajo
            y_start = None
            if y_obs_base is not None:
                y_start = y_obs_base + 12
            elif cedible_box:
                y_start = cedible_box[0].y1 + 20
            else:
                y_start = page_height * 0.55  # aproximación si no hay anclaje

            # Centrado horizontal
            x_left = max((page_width - foto_ancho_pt) / 2, margen)
            x_right = min(x_left + foto_ancho_pt, page_width - margen)
            # recalcula por si ajustamos a margen derecho
            foto_ancho_pt_real = x_right - x_left
            alto_pt_real = foto_ancho_pt_real * ratio

            # ¿Cabe en esta página?
            if y_start + alto_pt_real + margen <= page_height:
                target_rect = fitz.Rect(x_left, y_start, x_left + foto_ancho_pt_real, y_start + alto_pt_real)
                pagina.insert_image(target_rect, stream=foto_jpeg_bytes)
            else:
                # Crear nueva página y centrar arriba con márgenes
                new_page = doc.new_page(-1)  # al final
                pw, ph = new_page.rect.width, new_page.rect.height
                x_left = max((pw - foto_ancho_pt) / 2, margen)
                x_right = min(x_left + foto_ancho_pt, pw - margen)
                foto_ancho_pt_real = x_right - x_left
                alto_pt_real = foto_ancho_pt_real * ratio
                y_top = margen
                target_rect = fitz.Rect(x_left, y_top, x_left + foto_ancho_pt_real, y_top + alto_pt_real)
                new_page.insert_image(target_rect, stream=foto_jpeg_bytes)

        except Exception as e:
            # No rompemos el flujo si falla la foto
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

# ================= SUBIDA A DRIVE (PDF ÚNICO) ====================
def subir_a_drive(nombre_archivo, contenido_io, mime_type="application/pdf", parent_id="0AFh4pnUAC83dUk9PVA"):
    creds_info = st.secrets["gcp_service_account"]
    credentials = service_account.Credentials.from_service_account_info(creds_info)
    servicio = build("drive", "v3", credentials=credentials)

    file_metadata = {
        "name": nombre_archivo,
        "mimeType": mime_type,
        "parents": [parent_id],
    }
    contenido_io.seek(0)
    media = MediaIoBaseUpload(contenido_io, mimetype=mime_type)
    archivo = servicio.files().create(
        body=file_metadata,
        media_body=media,
        fields="id",
        supportsAllDrives=True
    ).execute()
    return archivo.get("id")

# ================= UI PRINCIPAL ====================
if pdf_file is not None:
    pdf_bytes = pdf_file.read()

    st.subheader("Vista previa del documento original:")
    img_preview_before = render_preview(pdf_bytes)
    st.image(img_preview_before, use_container_width=True)

    st.subheader("Dibuja tu firma aquí:")
    canvas_result = st_canvas(
        fill_color="rgba(0, 0, 0, 0)",
        stroke_width=2,
        stroke_color="black",
        background_color="#ffffff00",
        height=150,
        width=400,
        drawing_mode="freedraw",
        key="canvas"
    )

    signature_img = None
    if canvas_result.image_data is not None:
        signature_img = Image.fromarray((canvas_result.image_data).astype("uint8"))

    if st.button("Firmar Documento"):
        if signature_img is None:
            st.warning("⚠️ Dibuja tu firma primero.")
        elif not (nombre and recinto and fecha and rut and numero_guia):
            st.warning("⚠️ Completa todos los campos del formulario.")
        else:
            # Comprimir foto si viene
            foto_jpeg_bytes = None
            if foto_file is not None:
                try:
                    foto_jpeg_io = comprimir_imagen(foto_file.read(), max_lado=max_lado, calidad=calidad)
                    foto_jpeg_bytes = foto_jpeg_io.getvalue()
                    st.info(f"Foto comprimida lista para incrustar (≈ {len(foto_jpeg_bytes)//1024} KB).")
                except Exception as e:
                    st.error(f"No se pudo comprimir la foto: {e}")

            # Construir PDF final (firma + textos + foto incrustada)
            pdf_final_io = insertar_firma_y_texto_en_pdf(
                pdf_bytes=pdf_bytes,
                firma_img=signature_img,
                nombre=nombre,
                recinto=recinto,
                fecha_str=fecha_str,
                rut=rut,
                observacion=observacion,
                firma_width=120,
                foto_jpeg_bytes=foto_jpeg_bytes,
                foto_ancho_pt=ancho_en_pdf,
            )

            if pdf_final_io:
                st.success("✅ Documento firmado y foto incrustada correctamente.")

                with st.spinner("Subiendo PDF a Google Drive..."):
                    drive_id_pdf = subir_a_drive(f"{nombre_pdf}.pdf", pdf_final_io, mime_type="application/pdf")
                st.success("📄 PDF enviado a Google Drive con éxito.")

                st.subheader("Vista previa del documento final:")
                img_preview_after = render_preview(pdf_final_io.getvalue())
                st.image(img_preview_after, use_container_width=True)

                st.download_button(
                    label="Descargar Documento",
                    data=pdf_final_io,
                    file_name=f"{nombre_pdf}.pdf",
                    mime="application/pdf"
                )

st.markdown("""
---
<center style='color: gray;'>Desarrollado por Ingefix 2025</center>
""", unsafe_allow_html=True)

