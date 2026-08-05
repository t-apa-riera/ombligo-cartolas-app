import streamlit as st
import pandas as pd
import unicodedata
import re
from io import BytesIO
from rapidfuzz import fuzz

st.set_page_config(page_title="Conciliación Integral Ombligo", layout="centered")

def limpiar_texto_bancario(texto):
    if not isinstance(texto, str): return ""
    texto = unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('utf-8')
    texto = texto.upper().replace("TRASPASO DE:", "").replace("TRASPASO DE :", "")
    return " ".join(texto.split())

def limpiar_rut(rut):
    if pd.isna(rut): return ""
    rut = str(rut).upper()
    return re.sub(r'[^0-9K]', '', rut)

def calcular_similitud(nombre_inscrito, nombre_banco):
    n_inscrito = str(nombre_inscrito)
    n_banco = str(nombre_banco)
    return max(fuzz.token_set_ratio(n_inscrito, n_banco), fuzz.partial_ratio(n_inscrito, n_banco))

st.title("Conciliación Integral (Cartola + Formularios)")
st.markdown("Valida quién envió el formulario y cruza con el banco para ver si la plata realmente llegó.")

montos_input = st.text_input("Montos válidos para el mes, separados por coma (Ej: 17500, 19500)", "17500, 19500")

# 3 Bloques de subida de archivos
file_cartola = st.file_uploader("1. Sube la Cartola Bancaria (Excel/CSV)", type=['xlsx', 'xls', 'csv'])
file_inscripciones = st.file_uploader("2. Sube la Base de Inscripciones (Excel)", type=['xlsx'])
files_formularios = st.file_uploader("3. Sube los Formularios de Pago (Puedes arrastrar el Interno y Externo juntos)", type=['xlsx'], accept_multiple_files=True)

if st.button("Conciliar Todo") and file_cartola and file_inscripciones and files_formularios:
    with st.spinner("Cruzando Base, Formularios y Cartola del Banco..."):
        try:
            montos_aceptados = [int(m.strip()) for m in montos_input.split(',')]
            
            # --- 1. LEER Y UNIR TODOS LOS FORMULARIOS ---
            ruts_formulario = set()
            for file_form in files_formularios:
                df_f = pd.read_excel(file_form)
                # Buscar columna de RUT
                col_rut_f = [c for c in df_f.columns if 'rut' in str(c).lower()][0]
                ruts_formulario.update(df_f[col_rut_f].apply(limpiar_rut).tolist())
            ruts_formulario.discard("")
            
            # --- 2. LEER CARTOLA ---
            df_raw = pd.read_excel(file_cartola, header=None)
            fila_header = 0
            for i, row in df_raw.iterrows():
                row_str = " ".join([str(x).lower() for x in row.values])
                if "descrip" in row_str and "abono" in row_str:
                    fila_header = i
                    break
            
            df_cartola = pd.read_excel(file_cartola, skiprows=fila_header)
            col_abonos = [c for c in df_cartola.columns if 'abono' in str(c).lower()][0]
            col_desc = [c for c in df_cartola.columns if 'descrip' in str(c).lower()][0]
            col_fecha = [c for c in df_cartola.columns if 'fecha' in str(c).lower()][0]
            
            df_cartola = df_cartola.dropna(subset=[col_abonos])
            df_cartola = df_cartola[df_cartola[col_abonos] > 0].copy()
            df_cartola['Nombre_Limpio'] = df_cartola[col_desc].apply(limpiar_texto_bancario)
            df_cartola = df_cartola.rename(columns={col_fecha: 'Fecha', col_abonos: 'Monto', col_desc: 'Descripcion'})
            
            # --- 3. LEER INSCRIPCIONES (LA BASE OFICIAL) ---
            df_ins = pd.read_excel(file_inscripciones)
            cols_nombre_completo = [c for c in df_ins.columns if 'nombre completo' in str(c).lower()]
            col_nombre = cols_nombre_completo[0] if cols_nombre_completo else [c for c in df_ins.columns if 'nombre' in str(c).lower()][0]
            col_rut_ins = [c for c in df_ins.columns if 'rut' in str(c).lower()][0]
            
            df_ins['Nombre_Limpio'] = df_ins[col_nombre].apply(limpiar_texto_bancario)
            df_ins['RUT_Limpio'] = df_ins[col_rut_ins].apply(limpiar_rut)
            
            # --- 4. MOTOR DE CONCILIACIÓN A TRES BANDAS ---
            df_ins['Estado pago'] = '4. No pagado'
            df_ins['Lleno_Formulario'] = df_ins['RUT_Limpio'].isin(ruts_formulario)
            df_ins['Similitud Nombre%'] = 0.0
            df_ins['Fecha encontrada'] = None
            df_ins['Monto encontrado'] = None
            
            transferencias_usadas = set()
            
            for idx, row in df_ins.iterrows():
                nombre_inscrito = row['Nombre_Limpio']
                candidatos = []
                
                # Buscar en el banco
                for i, trans in df_cartola.iterrows():
                    if i not in transferencias_usadas and trans['Monto'] in montos_aceptados:
                        similitud = calcular_similitud(nombre_inscrito, trans['Nombre_Limpio'])
                        candidatos.append({
                            'index': i, 'similitud': similitud, 'monto': trans['Monto'], 'fecha': trans['Fecha']
                        })
                
                # Evaluar evidencia
                encontrado_en_banco = False
                if candidatos:
                    candidatos.sort(key=lambda x: x['similitud'], reverse=True)
                    mejor = candidatos[0]
                    if mejor['similitud'] >= 85: # Umbral IA
                        encontrado_en_banco = True
                        df_ins.at[idx, 'Similitud Nombre%'] = round(mejor['similitud'])
                        df_ins.at[idx, 'Fecha encontrada'] = mejor['fecha']
                        df_ins.at[idx, 'Monto encontrado'] = mejor['monto'] # Rescatando el monto encontrado
                        transferencias_usadas.add(mejor['index'])
                
                # Cruzar Banco vs Formulario
                if encontrado_en_banco and row['Lleno_Formulario']:
                    df_ins.at[idx, 'Estado pago'] = '1. Verificado (Form + Banco)'
                elif encontrado_en_banco and not row['Lleno_Formulario']:
                    df_ins.at[idx, 'Estado pago'] = '2. Despistado (Pago sin Formulario)'
                elif not encontrado_en_banco and row['Lleno_Formulario']:
                    df_ins.at[idx, 'Estado pago'] = '3. ALERTA: Con Formulario pero SIN PAGO en banco'
                else:
                    df_ins.at[idx, 'Estado pago'] = '4. No pagado'

            # --- 5. FILTRAR COLUMNAS PARA EL EXCEL FINAL ---
            # Renombramos las columnas originales a tu formato deseado
            df_ins = df_ins.rename(columns={
                col_nombre: 'Nombre completo',
                col_rut_ins: 'RUT'
            })
            
            columnas_deseadas = [
                'ID', 'Nombre completo', 'RUT', 'Carrera', 'Estado pago', 
                'Motivo', 'Similitud Nombre%', 'Monto encontrado', 'Fecha encontrada'
            ]
            
            # Aseguramos que todas las columnas existan, creando en blanco las que no (Ej. Motivo)
            for col in columnas_deseadas:
                if col not in df_ins.columns:
                    df_ins[col] = None
                    
            df_final = df_ins[columnas_deseadas]

            # --- 6. PREPARAR DESCARGAS ---
            verificados = df_final[df_final['Estado pago'] == '1. Verificado (Form + Banco)']
            despistados = df_final[df_final['Estado pago'] == '2. Despistado (Pago sin Formulario)']
            alertas = df_final[df_final['Estado pago'] == '3. ALERTA: Con Formulario pero SIN PAGO en banco']
            no_pagados = df_final[df_final['Estado pago'] == '4. No pagado']
            sobrantes = df_cartola.drop(index=list(transferencias_usadas))
            
            st.success(f"✅ {len(verificados)} Pagos Verificados (Tienen formulario y depósito).")
            st.info(f"💡 {len(despistados)} Despistados (Depositaron pero no enviaron formulario).")
            st.error(f"🚨 {len(alertas)} ALERTAS (Llenaron el formulario pero la plata no está en el banco).")
            st.warning(f"⚠️ {len(no_pagados)} Pendientes (No han hecho nada).")
            
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                verificados.to_excel(writer, index=False, sheet_name='Verificados')
                alertas.to_excel(writer, index=False, sheet_name='🚨 ALERTAS (Sin Pago)')
                despistados.to_excel(writer, index=False, sheet_name='Despistados (Falta Form)')
                no_pagados.to_excel(writer, index=False, sheet_name='No Pagados')
                sobrantes.to_excel(writer, index=False, sheet_name='Sobrantes Banco')
            
            st.download_button(
                label="Descargar Reporte Integral", 
                data=output.getvalue(), 
                file_name="Reporte_Conciliacion_Integral.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            
        except Exception as e:
            st.error(f"Error técnico detallado: {e}")
