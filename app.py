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



# Función para insertar firma y texto
def insertar_firma_y_texto_en_pdf(pdf_bytes, firma_img, nombre, recinto, fecha_str, rut, observacion, firma_width=150):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pagina = doc[-1]  # última página

    # Insertar texto
    pagina.insert_text((150, 685), nombre, fontsize=12, fontname="helv", fill=(0, 0, 0))
    pagina.insert_text((150, 698), recinto, fontsize=12, fontname="helv", fill=(0, 0, 0))
    pagina.insert_text((150, 708), fecha_str, fontsize=12, fontname="helv", fill=(0, 0, 0))
    pagina.insert_text((450, 698),  rut, fontsize=12, fontname="helv", fill=(0, 0, 0))
    pagina.draw_rect(fitz.Rect(150, 730, 480, 790), color=(0.7, 0.7, 0.7), width=0.5)
    pagina.insert_text((80, 750), "Observación:", fontsize=12, fontname="helv", fill=(0, 0, 0))
    pagina.insert_textbox(
        fitz.Rect(150, 730, 480, 790),
        observacion,
        fontsize=11,
        fontname="helv",
        fill=(0, 0, 0),
        align=0
    )

    # Convertir firma a PNG
    img_byte_arr = io.BytesIO()
    firma_img.save(img_byte_arr, format='PNG')
    img_bytes = img_byte_arr.getvalue()
    img = Image.open(io.BytesIO(img_bytes))
    w_orig, h_orig = img.size
    scale = firma_width / w_orig
    h_scaled = h_orig * scale

    # Posición firma
    x = 400
    y = 60
    rect = fitz.Rect(
        x,
        pagina.rect.height - y - h_scaled,
        x + firma_width,
        pagina.rect.height - y
    )

    pagina.insert_image(rect, stream=img_bytes)

    output_pdf = io.BytesIO()
    doc.save(output_pdf)
    doc.close()
    output_pdf.seek(0)
    return output_pdf

# Renderizar página PDF como imagen
def render_preview(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pagina = doc[-1]
    zoom = 6
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
        supportsAllDrives=True   # <--- esto es muy importante para Shared Drives
    ).execute()
    return archivo.get("id")

# Procesamiento principal
if pdf_file is not None:
    pdf_bytes = pdf_file.read()

    st.subheader("Vista previa Documento:")
    img_preview_before = render_preview(pdf_bytes)
    st.image(img_preview_before, use_container_width=True)

    # Área de firma
    st.subheader("Dibuje su firma aquí:")
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
        elif not (nombre and recinto and fecha and rut):
            st.warning("⚠️ Completa todos los campos de texto.")
        else:
            pdf_firmado_io = insertar_firma_y_texto_en_pdf(
                pdf_bytes, signature_img, nombre, recinto, fecha_str, rut, observacion
            )
            st.success("✅ Documento completado correctamente.")

            with st.spinner("Subiendo a Google Drive..."):
                drive_id = subir_a_drive(f"{nombre_pdf}.pdf", pdf_firmado_io)
            st.success(f"📤 PDF subido a Google Drive con ID: `{drive_id}`")

            st.subheader(" Vista previa con firma y datos:")
            img_preview_after = render_preview(pdf_firmado_io.getvalue())
            st.image(img_preview_after, use_container_width=True)


            st.download_button(
                label=" Descargar Documento Firmado",
                data=pdf_firmado_io,
                file_name=f"{nombre_pdf}.pdf",
                mime="application/pdf"
            )
            
