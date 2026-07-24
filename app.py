import streamlit as st
import pandas as pd
import re
from io import BytesIO

st.set_page_config(page_title="Conciliación Cartola Ombligo", layout="centered")

def limpiar_rut(rut):
    if pd.isna(rut):
        return ""
    rut = str(rut).upper()
    return re.sub(r'[^0-9K]', '', rut)

st.title("Conciliación por Cartola Bancaria")
st.markdown("Sube tu Cartola y la Base de Inscritos para cruzar los pagos mediante RUT y Nombre.")

file_cartola = st.file_uploader("1. Sube la Cartola Bancaria (Excel)", type=['xlsx', 'xls', 'csv'])
file_inscripciones = st.file_uploader("2. Sube la Base de Inscripciones (Excel)", type=['xlsx'])

if st.button("Conciliar Pagos") and file_cartola and file_inscripciones:
    with st.spinner("Analizando transferencias..."):
        try:
            # Leer archivos
            if file_cartola.name.endswith('.csv'):
                df_cartola = pd.read_csv(file_cartola)
            else:
                df_cartola = pd.read_excel(file_cartola)
                
            df_ins = pd.read_excel(file_inscripciones)
            
            # 1. Identificar columnas en Inscripciones
            col_rut_ins = [c for c in df_ins.columns if 'Rut' in c or 'RUT' in str(c).upper()]
            col_rut_ins = col_rut_ins[0] if col_rut_ins else None
            
            # Limpiar RUT de inscripciones
            if col_rut_ins:
                df_ins['RUT_Limpio'] = df_ins[col_rut_ins].apply(limpiar_rut)
            else:
                st.error("No se encontró columna de RUT en el archivo de inscripciones.")
                st.stop()
                
            # 2. Lógica de conciliación simplificada (Busca RUTs en toda la cartola)
            cartola_text = df_cartola.to_string().upper()
            
            # Buscar quiénes de los inscritos aparecen en la cartola
            df_ins['Encontrado_en_Cartola'] = df_ins['RUT_Limpio'].apply(lambda r: r in cartola_text if r != "" else False)
            
            # Filtrar resultados
            df_pagados = df_ins[df_ins['Encontrado_en_Cartola']].copy()
            df_pendientes = df_ins[~df_ins['Encontrado_en_Cartola']].copy()
            
            # Mostrar resultados
            st.success(f"¡Se encontraron {len(df_pagados)} pagos en la cartola!")
            st.warning(f"Quedan {len(df_pendientes)} personas pendientes de pago.")
            
            # Preparar descarga
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df_pendientes.to_excel(writer, index=False, sheet_name='Pendientes')
                df_pagados.to_excel(writer, index=False, sheet_name='Pagados')
            processed_data = output.getvalue()
            
            st.download_button(
                label="Descargar Reporte de Conciliación",
                data=processed_data,
                file_name="Reporte_Conciliacion_Cartola.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            
        except Exception as e:
            st.error(f"Ocurrió un error al procesar los archivos: {e}")
