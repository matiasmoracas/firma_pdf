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
st.title("Firmas Guías de Salida Ingefix")

# Subir PDF
pdf_file = st.file_uploader("Subir La Guía de Salida", type=["pdf"])

# Campos del formulario
nombre = st.text_input("Nombre")
recinto = st.text_input("Recinto")
fecha = st.date_input("Fecha", value=datetime.date.today())
fecha_str = fecha.strftime("%d-%m-%Y")
rut = st.text_input("RUT")
observacion = st.text_area("Observación")

iniciales_chofer = st.selectbox("Iniciales del Chofer", ["MOC", "BFS", "MFV"])
numero_guia = st.text_input("Número de la Guía", "")
nombre_pdf = f"GS {numero_guia} {iniciales_chofer}"


# ================= FUNCIÓN PARA MODIFICAR EL PDF ====================
def insertar_firma_y_texto_en_pdf(pdf_bytes, firma_img, nombre, recinto, fecha_str, rut, observacion, firma_width=120):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pagina = doc[-1]  # última página

    # Función auxiliar para insertar texto al lado de un campo buscado
    def insertar_dato_campo(etiqueta, texto, offset_x=5, offset_y=4):
        resultados = pagina.search_for(etiqueta)
        if resultados:
            box = resultados[0]
            x = box.x1 + offset_x
            y = box.y0 + offset_y
            pagina.insert_text((x, y), texto, fontsize=11, fontname="helv", fill=(0, 0, 0))

    # Insertar datos con ajustes de altura
    insertar_dato_campo("Nombre:", nombre, offset_y=4)
    insertar_dato_campo("Recinto:", recinto, offset_y=7)  # 3px más abajo que nombre
    insertar_dato_campo("RUT:", rut, offset_y=4)
    insertar_dato_campo("Fecha:", fecha_str, offset_y=8)  # más abajo que recinto

    # Insertar firma al lado de "Firma"
    firma_box = pagina.search_for("Firma")
    if firma_box:
        rect = firma_box[0]
        x = rect.x1 + 5
        y = rect.y0 - 12  # buen ajuste visual

        img_bytes = io.BytesIO()
        firma_img.save(img_bytes, format='PNG')
        img_bytes = img_bytes.getvalue()

        image = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        w_orig, h_orig = image.size
        escala = firma_width / w_orig
        h_escala = h_orig * escala

        firma_rect = fitz.Rect(x, y, x + firma_width, y + h_escala)
        pagina.insert_image(firma_rect, stream=img_bytes)

    # Insertar observación centrada debajo de "CEDIBLE"
    cedible_box = pagina.search_for("CEDIBLE")
    if cedible_box and observacion.strip():
        cbox = cedible_box[0]
        ancho_pagina = pagina.rect.width
        ancho_texto = 250
        alto_texto = 40
        x_center = (ancho_pagina - ancho_texto) / 2
        y_obs = cbox.y1 + 5

        pagina.insert_textbox(
            fitz.Rect(x_center, y_obs, x_center + ancho_texto, y_obs + alto_texto),
            observacion.strip(),
            fontsize=10,
            fontname="helv",
            align=1,
            fill=(0, 0, 0)
        )

    output = io.BytesIO()
    doc.save(output)
    doc.close()
    output.seek(0)
    return output


# =============== FUNCIONES ADICIONALES =====================
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

    file_metadata = {
        "name": nombre_archivo,
        "mimeType": "application/pdf",
        "parents": ["0AFh4pnUAC83dUk9PVA"]
    }
    contenido_pdf.seek(0)
    media = MediaIoBaseUpload(contenido_pdf, mimetype="application/pdf")
    archivo = servicio.files().create(
        body=file_metadata,
        media_body=media,
        fields="id",
        supportsAllDrives=True
    ).execute()
    return archivo.get("id")


# =================== INTERFAZ PRINCIPAL ====================
if pdf_file is not None:
    pdf_bytes = pdf_file.read()

    st.subheader("Vista previa del documento original:")
    img_preview_before = render_preview(pdf_bytes)
    st.image(img_preview_before, use_container_width=True)

    # Firma
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
            pdf_firmado_io = insertar_firma_y_texto_en_pdf(
                pdf_bytes, signature_img, nombre, recinto, fecha_str, rut, observacion
            )
            if pdf_firmado_io:
                st.success("✅ Documento completado correctamente.")

                with st.spinner("Subiendo a Google Drive..."):
                    drive_id = subir_a_drive(f"{nombre_pdf}.pdf", pdf_firmado_io)
                st.success(f"📤 PDF subido a Google Drive con ID: `{drive_id}`")

                st.subheader("Vista previa del documento firmado:")
                img_preview_after = render_preview(pdf_firmado_io.getvalue())
                st.image(img_preview_after, use_container_width=True)

                st.download_button(
                    label="📥 Descargar Documento Firmado",
                    data=pdf_firmado_io,
                    file_name=f"{nombre_pdf}.pdf",
                    mime="application/pdf"
                )
