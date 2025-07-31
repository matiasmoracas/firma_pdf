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

# Campos de texto
nombre = st.text_input("Nombre")
recinto = st.text_input("Recinto")
fecha = st.date_input("Fecha", value=datetime.date.today())
fecha_str = fecha.strftime("%d-%m-%Y")
rut = st.text_input("RUT")
observacion = st.text_area("Observación")

# Nombre del archivo firmado
iniciales_chofer = st.selectbox("Iniciales del Chofer", ["MOC", "BFS", "MFV"])
numero_guia = st.text_input("Número de la Guía", "")
nombre_pdf = f"GS {numero_guia} {iniciales_chofer}"


# ======= FUNCIÓN PARA INSERTAR TEXTO, FIRMA Y OBSERVACIÓN ===========
def insertar_firma_y_texto_en_pdf(pdf_bytes, firma_img, nombre, recinto, fecha_str, rut, observacion, firma_width=120):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pagina = doc[-1]  # última página

    # Buscar "Nombre:" como ancla para datos principales
    cajas = pagina.search_for("Nombre:")
    if not cajas:
        st.error("No se encontró 'Nombre:' en el PDF.")
        return None

    base = cajas[0]
    x_inicio = base.x1 + 10
    y_inicio = base.y0
    salto_linea = 18

    # Insertar datos
    pagina.insert_text((x_inicio, y_inicio), nombre, fontsize=11, fill=(0, 0, 0))
    pagina.insert_text((x_inicio, y_inicio + salto_linea), recinto, fontsize=11, fill=(0, 0, 0))
    pagina.insert_text((x_inicio + 220, y_inicio + salto_linea), rut, fontsize=11, fill=(0, 0, 0))
    pagina.insert_text((x_inicio, y_inicio + salto_linea * 2), fecha_str, fontsize=11, fill=(0, 0, 0))

    # Insertar firma
    img_byte_arr = io.BytesIO()
    firma_img.save(img_byte_arr, format='PNG')
    img_bytes = img_byte_arr.getvalue()
    firma_pil = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    w_orig, h_orig = firma_pil.size
    escala = firma_width / w_orig
    h_escala = h_orig * escala

    x_firma = x_inicio + 220
    y_firma = y_inicio + salto_linea * 2 - 5
    rect = fitz.Rect(x_firma, y_firma, x_firma + firma_width, y_firma + h_escala)
    pagina.insert_image(rect, stream=img_bytes)

    # Insertar Observación debajo de "CEDIBLE"
    cedible_box = pagina.search_for("CEDIBLE")
    if cedible_box:
        cbox = cedible_box[0]
        ancho_pagina = pagina.rect.width
        ancho_texto = 330
        alto_texto = 60
        x_center = (ancho_pagina - ancho_texto) / 2
        y_obs = cbox.y1 + 10  # debajo de CEDIBLE

        pagina.insert_textbox(
            fitz.Rect(x_center, y_obs, x_center + ancho_texto, y_obs + alto_texto),
            observacion,
            fontsize=11,
            fontname="helv",
            align=1,  # centrado
            fill=(0, 0, 0)
        )

    # Guardar PDF en memoria
    output = io.BytesIO()
    doc.save(output)
    doc.close()
    output.seek(0)
    return output


# Vista previa como imagen
def render_preview(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pagina = doc[-1]
    zoom = 4
    mat = fitz.Matrix(zoom, zoom)
    pix = pagina.get_pixmap(matrix=mat)
    img_data = pix.tobytes("png")
    doc.close()
    return img_data


# Subir a Google Drive
def subir_a_drive(nombre_archivo, contenido_pdf):
    creds_info = st.secrets["gcp_service_account"]
    credentials = service_account.Credentials.from_service_account_info(creds_info)
    servicio = build("drive", "v3", credentials=credentials)

    file_metadata = {
        "name": nombre_archivo,
        "mimeType": "application/pdf",
        "parents": ["0AFh4pnUAC83dUk9PVA"]  # carpeta destino
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


# ===================== PROCESAMIENTO PRINCIPAL =========================
if pdf_file is not None:
    pdf_bytes = pdf_file.read()

    st.subheader("Vista previa del documento original:")
    img_preview_before = render_preview(pdf_bytes)
    st.image(img_preview_before, use_container_width=True)

    # Canvas para la firma
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

