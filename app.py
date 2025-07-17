import streamlit as st
from streamlit_drawable_canvas import st_canvas
from PIL import Image
import io
import fitz  # PyMuPDF

st.set_page_config(page_title="Firmas Guías de Salida Ingefix", layout="centered")
st.title("Firmas Guías de Salida Ingefix")

# Subir PDF
pdf_file = st.file_uploader("Subir La Guía de Salida", type=["pdf"])

# Input para que usuario elija nombre del PDF firmado (sin extensión)
nombre_pdf = st.text_input("Nombre para guardar la Guía Firmada", "GUIA N°")

# Variable zoom para oder hacer zoom desde la app subida a streamit cloud 
tamano_zoom = st.slider("Zoom de vista previa", min_value=1, max_value=5, value=2)

# Función para insertar la firma exactamente donde dice "Firma :"
def insertar_firma_en_pdf(pdf_bytes, firma_img, firma_width=150):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pagina = doc[-1]  # última página

    # Convertir firma a PNG
    img_byte_arr = io.BytesIO()
    firma_img.save(img_byte_arr, format='PNG')
    img_bytes = img_byte_arr.getvalue()

    # Escalar tamaño de firma
    img = Image.open(io.BytesIO(img_bytes))
    w_orig, h_orig = img.size
    scale = firma_width / w_orig
    h_scaled = h_orig * scale

    # COORDENADAS EXACTAS para que calce con "Firma :"
    x = 400 # distancia desde borde izquierdo
    y = 60 # distancia desde borde inferior

    rect = fitz.Rect(
        x,
        pagina.rect.height - y - h_scaled,
        x + firma_width,
        pagina.rect.height - y
    )

    # Insertar la firma en el PDF
    pagina.insert_image(rect, stream=img_bytes)

    output_pdf = io.BytesIO()
    doc.save(output_pdf)
    doc.close()
    output_pdf.seek(0)
    return output_pdf

# Renderizar la última página del PDF como imagen
def render_preview(pdf_bytes, zoom=2):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pagina = doc[-1]
    mat = fitz.Matrix(zoom, zoom)
    pix = pagina.get_pixmap(matrix=mat)
    img_data = pix.tobytes("png")
    doc.close()
    return img_data

if pdf_file is not None:
    pdf_bytes = pdf_file.read()

    # Vista previa antes de firmar
    st.subheader("Vista previa Documento:")
    img_preview_before = render_preview(pdf_bytes, zoom=tamano_zoom) # para que se pueda hacer zoom desde la app
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

    # Capturar imagen de firma
    signature_img = None
    if canvas_result.image_data is not None:
        signature_img = Image.fromarray((canvas_result.image_data).astype("uint8"))

    # Botón para firmar el PDF y descargar con nombre personalizado
    if st.button("Firmar Documento"):
        if signature_img is None:
            st.warning("Dibuja tu firma primero.")
        else:
            pdf_firmado_io = insertar_firma_en_pdf(pdf_bytes, signature_img)
            st.success("Documento firmado correctamente.")

            # Vista previa con firma (imagen)
            st.subheader("Vista previa Documento firmado:")
            img_preview_after = render_preview(pdf_firmado_io.getvalue())
            st.image(img_preview_after, use_container_width=True)

            # Botón para descargar con nombre personalizado
            st.download_button(
                label=" Descargar Documento Firmado",
                data=pdf_firmado_io,
                file_name=f"{nombre_pdf}.pdf",
                mime="application/pdf"
            )
