import streamlit as st
from Conexion_LLM_SQL import chat
import uuid  # Para generar un thread_id único

# Configuración de la página de Streamlit
st.set_page_config(page_title="Chatbot", page_icon="🤖")

st.title("🤖 Chatbot")
st.write("Interactúa con el ChatBot preguntandole lo que desees sobre tu base de datos.")

# Generar un thread_id único para cada sesión de usuario
if "thread_id" not in st.session_state:
    st.session_state["thread_id"] = str(uuid.uuid4())  # Generar un identificador único

# Historial de la conversación almacenado en la sesión
if "historial" not in st.session_state:
    st.session_state["historial"] = []  # Inicializamos una lista vacía si no existe

# Caja de texto para la entrada del usuario
pregunta_usuario = st.text_input("Escribe tu pregunta:")

# Función para manejar la interacción con el chatbot
def enviar_mensaje():
    if pregunta_usuario:
        # Llamamos a la función 'chat' del archivo Backend.py, pasando el thread_id
        respuesta = chat(pregunta_usuario, st.session_state["thread_id"])
        # Almacenar la conversación en el historial de la sesión
        st.session_state["historial"].append({"pregunta": pregunta_usuario, "respuesta": respuesta})

# Botón para enviar la pregunta
if st.button("Enviar"):
    enviar_mensaje()  # Llamamos a la función que maneja el envío del mensaje

# Mostrar el historial de la conversación
if st.session_state["historial"]:
    st.write("### Historial de la conversación:")
    for chat in st.session_state["historial"]:
        st.write(f"Tú: {chat['pregunta']}")
        st.write(f"Chat: {chat['respuesta']}")