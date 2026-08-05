import streamlit as st
import pandas as pd
import unicodedata
import re
from io import BytesIO
from rapidfuzz import fuzz

st.set_page_config(page_title="Conciliación Integral", layout="centered")

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
st.markdown("Valida quién envió el formulario y cruza con el banco. **Los montos se extraen automáticamente de los formularios.**")

# 3 Bloques de subida de archivos
file_cartola = st.file_uploader("1. Sube la Cartola Bancaria (Excel/CSV)", type=['xlsx', 'xls', 'csv'])
file_inscripciones = st.file_uploader("2. Sube la Base de Inscripciones (Excel)", type=['xlsx'])
files_formularios = st.file_uploader("3. Sube los Formularios de Pago (Puedes arrastrar el Interno y Externo juntos)", type=['xlsx'], accept_multiple_files=True)

if st.button("Conciliar Todo") and file_cartola and file_inscripciones and files_formularios:
    with st.spinner("Extrayendo montos y cruzando datos..."):
        try:
            # --- 1. LEER FORMULARIOS Y EXTRAER MONTOS AUTOMÁTICAMENTE ---
            ruts_formulario = {} # Diccionario: RUT -> Monto esperado
            montos_aceptados = set()
            
            for file_form in files_formularios:
                df_f = pd.read_excel(file_form)
                
                # Buscar columna de RUT
                col_rut_f = [c for c in df_f.columns if 'rut' in str(c).lower()][0]
                
                # Buscar la columna que contenga el método de pago o monto
                col_pago = None
                for c in df_f.columns:
                    c_str = str(c).lower()
                    if any(k in c_str for k in ['pago', 'monto', 'pagar', 'opción', 'opcion', 'metodo', 'método']):
                        col_pago = c
                        break
                
                for _, row_f in df_f.iterrows():
                    rut = limpiar_rut(row_f[col_rut_f])
                    if not rut: continue
                    
                    monto_esperado = 0
                    if col_pago:
                        # Extraer solo los números (ej: "Transferencia - $17.500" -> 17500)
                        val_str = str(row_f[col_pago]).replace('.', '').replace('$', '')
                        numeros = re.findall(r'\d+', val_str)
                        for n in numeros:
                            if int(n) >= 1000: # Asumimos que los cobros son mayores a $1000
                                monto_esperado = int(n)
                                montos_aceptados.add(monto_esperado)
                                break
                    
                    ruts_formulario[rut] = monto_esperado

            if montos_aceptados:
                st.success(f"✅ Montos detectados automáticamente en formularios: {', '.join([f'${m}' for m in montos_aceptados])}")
            else:
                st.warning("⚠️ No se detectaron montos válidos en los formularios. El cruce podría fallar.")

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
            df_ins['Similitud Nombre%'] = 0.0
            df_ins['Fecha encontrada'] = None
            df_ins['Monto encontrado'] = None
            
            transferencias_usadas = set()
            
            for idx, row in df_ins.iterrows():
                rut_actual = row['RUT_Limpio']
                nombre_inscrito = row['Nombre_Limpio']
                
                lleno_form = rut_actual in ruts_formulario
                monto_form = ruts_formulario.get(rut_actual, 0)
                
                candidatos = []
                
                # Buscar en el banco filtrando por los montos que detectamos
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
                        df_ins.at[idx, 'Monto encontrado'] = mejor['monto']
                        transferencias_usadas.add(mejor['index'])
                
                # Cruzar Banco vs Formulario con validación estricta de monto
                if encontrado_en_banco:
                    if lleno_form:
                        if monto_form > 0 and mejor['monto'] != monto_form:
                            df_ins.at[idx, 'Estado pago'] = f'3. ALERTA: Pagó ${int(mejor["monto"])} pero form dice ${monto_form}'
                        else:
                            df_ins.at[idx, 'Estado pago'] = '1. Verificado (Form + Banco)'
                    else:
                        df_ins.at[idx, 'Estado pago'] = '2. Despistado (Pago sin Formulario)'
                elif lleno_form:
                    df_ins.at[idx, 'Estado pago'] = '3. ALERTA: Con Formulario pero SIN PAGO en banco'
                else:
                    df_ins.at[idx, 'Estado pago'] = '4. No pagado'

            # --- 5. FILTRAR COLUMNAS PARA EL EXCEL FINAL ---
            df_ins = df_ins.rename(columns={
                col_nombre: 'Nombre completo',
                col_rut_ins: 'RUT'
            })
            
            # Formato estricto de las columnas que solicitaste
            columnas_deseadas = [
                'ID', 'Nombre completo', 'RUT', 'Carrera', 'Estado pago', 
                'Motivo', 'Similitud Nombre%', 'Monto encontrado', 'Fecha encontrada'
            ]
            
            # Aseguramos que existan, creando vacías si faltan en tu base original
            for col in columnas_deseadas:
                if col not in df_ins.columns:
                    df_ins[col] = None
                    
            df_final = df_ins[columnas_deseadas]

            # --- 6. PREPARAR DESCARGAS ---
            verificados = df_final[df_final['Estado pago'] == '1. Verificado (Form + Banco)']
            despistados = df_final[df_final['Estado pago'] == '2. Despistado (Pago sin Formulario)']
            alertas = df_final[df_final['Estado pago'].str.contains('3. ALERTA', na=False)]
            no_pagados = df_final[df_final['Estado pago'] == '4. No pagado']
            sobrantes = df_cartola.drop(index=list(transferencias_usadas))
            
            st.success(f"✅ {len(verificados)} Pagos Verificados sin problemas.")
            st.info(f"💡 {len(despistados)} Despistados (Depositaron pero no enviaron formulario).")
            if len(alertas) > 0:
                st.error(f"🚨 {len(alertas)} ALERTAS (Plata no llegó o montos no cuadran).")
            st.warning(f"⚠️ {len(no_pagados)} Pendientes (No han hecho nada).")
            
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                verificados.to_excel(writer, index=False, sheet_name='Verificados')
                alertas.to_excel(writer, index=False, sheet_name='🚨 ALERTAS')
                despistados.to_excel(writer, index=False, sheet_name='Despistados (Falta Form)')
                no_pagados.to_excel(writer, index=False, sheet_name='No Pagados')
                sobrantes.to_excel(writer, index=False, sheet_name='Sobrantes Banco')
            
            st.download_button(
                label="📥 Descargar Reporte Integral", 
                data=output.getvalue(), 
                file_name="Reporte_Conciliacion_Integral.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            
        except Exception as e:
            st.error(f"Error técnico detallado: {e}")
