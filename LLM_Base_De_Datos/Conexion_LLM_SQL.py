import os
from operator import itemgetter
from langchain_core.prompts import PromptTemplate
from langchain_community.llms import OpenAI
from langchain_community.utilities import SQLDatabase
from langchain_openai import ChatOpenAI
from langchain_community.tools.sql_database.tool import QuerySQLDataBaseTool
from langchain.chains import create_sql_query_chain
from sqlalchemy import create_engine, MetaData
from collections import deque
import uuid
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from API import Clave
from sqlalchemy.exc import SQLAlchemyError
import re

# Configuración del servidor y base de datos
server_name = 'LOLO\SQLEXPRESS'
database_name = 'Ventas2'

os.environ['OPENAI_API_KEY'] = Clave.OPENAI_API_KEY

# Crear un motor de conexión con SQLAlchemy
engine = create_engine(f"mssql+pyodbc://{server_name}/{database_name}?driver=ODBC+Driver+17+for+SQL+Server")

# Reflejar el esquema de la base de datos
metadata = MetaData()
metadata.reflect(bind=engine)

# Extraer información del esquema para el prompt
esquema = ""
for table in metadata.sorted_tables:
    esquema += f"Tabla: {table.name}\n"
    for column in table.columns:
        esquema += f"    Columna: {column.name} - Tipo: {column.type}\n"

# Crear el objeto SQLDatabase requerido por LangChain
db = SQLDatabase(engine)

class CustomMemorySaver:
    def __init__ (self):  
        self.memoria = {}

    def save(self, thread_id, mensaje):
        if thread_id not in self.memoria:
            self.memoria[thread_id] = deque(maxlen=50)
        self.memoria[thread_id].append(mensaje)

    def retrieve(self, thread_id):
        return list(self.memoria.get(thread_id, []))

memory = CustomMemorySaver()

# Inicializar el modelo de lenguaje LLM
llm = ChatOpenAI(model="gpt-4o", temperature=0.0)

# Configurar las herramientas de consulta SQL
execute_query = QuerySQLDataBaseTool(db=db)
write_query = create_sql_query_chain(llm, db)
chain = write_query | execute_query

# Función para construir el historial de la conversación
def construir_historial(historial, pregunta_actual):
    historial_conversacion = "\n".join(
        f"Usuario: {mensaje['content']}\nAsistente: {mensaje['response']}"
        for mensaje in historial
    )
    return f"""
    Historial de conversación:
    {historial_conversacion}

    Pregunta actual:
    {pregunta_actual}
    """
     
def validar_sql_con_db(resultado, pregunta, log_file="sql_queries.log"):
    palabras_error = ["Error", "sintaxis", "errores de sintaxis", "versión corregida", "Select"]
    
    #Busque si se encuentra las palabras en la repuesta
    if any(palabra in str(resultado) for palabra in palabras_error):
        # Escribe la consulta que genero probelmas
        with open(log_file, "a") as log:
            log.write(f"Pregunta: {pregunta}\n")
            log.write(f"Resultado con error: {resultado}\n")
            log.write("=" * 50 + "\n")
        return False #Error
    return True #No hay error


def validar_respuesta(texto):
    # Lista de patrones comunes para detectar código
    patrones_codigo = [
        r"```",        # Bloques de código en Markdown
        r"<code>",     # Etiquetas HTML de código
        r"\bSELECT\b", # Palabras clave SQL comunes
        r"\bFROM\b",
        r"\bWHERE\b",
        r"\bORDER BY\b",
    ]
    for patron in patrones_codigo:
        if re.search(patron, texto, re.IGNORECASE):
            return False
    return True

def reintentar_consulta(query_txt, pregunta):
    # Lee la consulta original desde el archivo
    with open(query_txt, "r") as file:
        consulta = file.read()

    # Inicializa una variable para capturar el mensaje de error
    mensaje_error = ""

    try:
        # Intenta ejecutar la consulta original
        resultado_sql = execute_query.invoke(consulta)
    except SQLAlchemyError as e:
        # Captura errores de SQLAlchemy (errores de sintaxis o ejecución en la BD)
        mensaje_error = f"Error de la base de datos: {str(e)}"
    except Exception as e:
        # Captura otros errores que puedan ocurrir
        mensaje_error = f"Error inesperado: {str(e)}"

    # Genera el prompt para corrección, incluyendo el mensaje de error si existe
    prompt_correccion = f"""
    Observa el error generado por la base de datos y corrige la consulta SQL:
    Error reportado:
    {mensaje_error}

    Consulta original:
    {consulta}

    Corrige la consulta SQL para que no tenga errores de sintaxis.
    """
    query_prompt_correccion = f"{prompt_correccion.format(question=pregunta)}"

    # Pide al LLM que genere una corrección
    query_generado_correcion = write_query.invoke({"question": query_prompt_correccion})

    # Ajusta el formato del resultado si es necesario
    query_generado_correcion = query_generado_correcion[7:-3]

    try:
        # Intenta ejecutar la consulta corregida
        resultado_sql_corregido = execute_query.invoke(query_generado_correcion)
        resultado_sql_str_corregido = str(resultado_sql_corregido)
    except SQLAlchemyError as e:
        # Captura errores de la consulta corregida
        resultado_sql_str_corregido = f"Error en la consulta corregida: {str(e)}"
    except Exception as e:
        # Captura otros errores inesperados
        resultado_sql_str_corregido = f"Error inesperado en la consulta corregida: {str(e)}"

    # Genera el contexto final para el LLM
    datos_resultado_correccion = f"""
        {prompt_correccion}
        Consulta SQL corregida: {query_generado_correcion}
        Resultado SQL: {resultado_sql_str_corregido}
    """
    respuesta_obj_correccion = llm.invoke(datos_resultado_correccion)

    # Extrae el contenido de la respuesta
    respuesta_texto_correccion = getattr(respuesta_obj_correccion, 'content', str(respuesta_obj_correccion))
    
    with open(query_txt, "w") as file:
        file.write(query_generado_correcion)

    return query_generado_correcion


def chat(pregunta, thread_id):
    try:
        #Generar el id
        historial = memory.retrieve(thread_id)
        
        #Construir el historial
        prompt_con_historial = construir_historial(historial, pregunta)

        # Crear el prompt para el modelo
        answer_prompt = PromptTemplate.from_template(
            f"""
            Contexto:
            - Bajo ninguna circunstancia debes ejecutar comandos que modifiquen, eliminen o alteren datos en la base de datos.
            - Si el usuario solicita realizar una acción que implique cambios en los datos (como INSERT, UPDATE o DELETE), responde que no tienes permisos para realizar dichas acciones.
            - Este es el esquema de la base de datos: {esquema}.
            - Historial de conversación: {prompt_con_historial}.
            - Utiliza la memoria para mantener la coherencia y responde de manera clara.
            Pregunta actual: {{question}}
            """
        )
        #Se define una variable para pasar el promt
        query_prompt = f"{answer_prompt.format(question=pregunta)}"
        
        #Se crea la consulta
        query_generado = write_query.invoke({"question": query_prompt})
        
        #Se elimina los caracteres no deseados
        query_generado = query_generado[7:-3]

        query_file = "query_temp.txt"
        with open(query_file, "w") as file:
            file.write(query_generado)

        resultado_sql = execute_query.invoke(query_generado)

        if not validar_sql_con_db(resultado_sql, pregunta):
            query_generado = reintentar_consulta(query_file, pregunta)
            resultado_sql = execute_query.invoke(query_generado)

        if not validar_sql_con_db(resultado_sql, pregunta):
            return "Ocurrió un error. Inténtelo de nuevo."
        

        resultado_sql_str = str(resultado_sql)
        
        #Se crea la repuesta
        datos_resultado = f"""
        {prompt_con_historial}
        Consulta SQL: {query_generado}
        Resultado SQL: {resultado_sql_str}
        """
        #Se llama al LLM para generar la repuesta
        respuesta_obj = llm.invoke(datos_resultado)
        respuesta_texto = getattr(respuesta_obj, 'content', str(respuesta_obj))
        
        if not validar_respuesta(respuesta_texto):
            return "La respuesta contiene código y no se puede mostrar. Reformula la pregunta por favor."
        
        #Se guarda el historial en la memoria
        memory.save(thread_id, {"content": pregunta, "response": respuesta_texto})
        return respuesta_texto

    except Exception as e:
        with open("error_log.log", "a") as log:
            log.write(f"Error: {str(e)}\n")
            return "Ocurrió un error inesperado. Por favor, contacte al administrador."