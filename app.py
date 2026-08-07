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
st.markdown("Valida quién envió el formulario y cruza con el banco usando un motor de 2 pasadas para evitar falsos positivos.")

file_cartola = st.file_uploader("1. Sube la Cartola Bancaria (Excel/CSV)", type=['xlsx', 'xls', 'csv'])
file_inscripciones = st.file_uploader("2. Sube la Base de Inscripciones (Excel)", type=['xlsx'])
files_formularios = st.file_uploader("3. Sube los Formularios de Pago (Interno y Externo)", type=['xlsx'], accept_multiple_files=True)

if st.button("Conciliar Todo") and file_cartola and file_inscripciones and files_formularios:
    with st.spinner("Conciliando con motor de 2 pasadas..."):
        try:
            # --- 1. LEER FORMULARIOS Y EXTRAER MONTOS (CORREGIDO) ---
            ruts_formulario = {} 
            
            for file_form in files_formularios:
                df_f = pd.read_excel(file_form)
                col_rut_f = [c for c in df_f.columns if 'rut' in str(c).lower()][0]
                
                col_pago = None
                for c in df_f.columns:
                    if any(k in str(c).lower() for k in ['forma de pago', 'pago', 'monto']):
                        col_pago = c
                        break
                
                for _, row_f in df_f.iterrows():
                    rut = limpiar_rut(row_f[col_rut_f])
                    if not rut: continue
                    
                    monto_esperado = 0
                    if col_pago and pd.notna(row_f[col_pago]):
                        val_str = str(row_f[col_pago]).replace('.', '')
                        numeros = re.findall(r'\d+', val_str)
                        for n in numeros:
                            if int(n) >= 1000: 
                                monto_esperado = int(n)
                                break
                    ruts_formulario[rut] = monto_esperado

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
            
            def formatear_fecha(f):
                try:
                    if isinstance(f, pd.Timestamp): return f.strftime('%d/%m')
                    return str(f).split(' ')[0] 
                except:
                    return str(f)
                    
            df_cartola[col_fecha] = df_cartola[col_fecha].apply(formatear_fecha)
            df_cartola = df_cartola.rename(columns={col_fecha: 'Fecha', col_abonos: 'Monto', col_desc: 'Descripcion'})

            # --- 3. LEER INSCRIPCIONES ---
            df_ins = pd.read_excel(file_inscripciones)
            cols_nombre_completo = [c for c in df_ins.columns if 'nombre completo' in str(c).lower()]
            col_nombre = cols_nombre_completo[0] if cols_nombre_completo else [c for c in df_ins.columns if 'nombre' in str(c).lower()][0]
            col_rut_ins = [c for c in df_ins.columns if 'rut' in str(c).lower()][0]
            
            df_ins['Nombre_Limpio'] = df_ins[col_nombre].apply(limpiar_texto_bancario)
            df_ins['RUT_Limpio'] = df_ins[col_rut_ins].apply(limpiar_rut)
            
            # Preparar columnas
            df_ins['Estado Pago'] = 'No pagado'
            df_ins['Motivo'] = ''
            df_ins['Similitud Nombre %'] = 0.0
            df_ins['Fecha Encontrada'] = None
            df_ins['Monto Encontrado'] = None
            
            transferencias_usadas = set()

            # --- 4. MOTOR DE 2 PASADAS ---
            
            # PASADA 1: CALCE PERFECTO (Nombre > 85% Y Monto Exacto)
            # Objetivo: Asegurar los pagos limpios sin que nadie se robe la plata de otro
            for idx, row in df_ins.iterrows():
                rut = row['RUT_Limpio']
                lleno_form = rut in ruts_formulario
                monto_form = ruts_formulario.get(rut, 0)
                
                if lleno_form and monto_form > 0:
                    for i, trans in df_cartola.iterrows():
                        if i not in transferencias_usadas and trans['Monto'] == monto_form:
                            similitud = calcular_similitud(row['Nombre_Limpio'], trans['Nombre_Limpio'])
                            if similitud >= 85:
                                df_ins.at[idx, 'Estado Pago'] = 'Pagado verificado'
                                df_ins.at[idx, 'Motivo'] = f"Aprobado (Pasada 1). Coincidencia: {round(similitud)}% | Fecha: {trans['Fecha']}"
                                df_ins.at[idx, 'Similitud Nombre %'] = round(similitud)
                                df_ins.at[idx, 'Fecha Encontrada'] = trans['Fecha']
                                df_ins.at[idx, 'Monto Encontrado'] = trans['Monto']
                                transferencias_usadas.add(i)
                                break # Ya lo encontramos, pasamos al siguiente inscrito

            # PASADA 2: DETECCIÓN DE PROBLEMAS Y TERCEROS PAGADORES
            # Objetivo: Evaluar a los que no pasaron la prueba estricta usando las transferencias sobrantes
            for idx, row in df_ins.iterrows():
                if df_ins.at[idx, 'Estado Pago'] == 'Pagado verificado':
                    continue # Saltamos a los que ya están listos
                
                rut = row['RUT_Limpio']
                lleno_form = rut in ruts_formulario
                monto_form = ruts_formulario.get(rut, 0)
                nombre_inscrito = row['Nombre_Limpio']
                
                candidatos = []
                for i, trans in df_cartola.iterrows():
                    if i not in transferencias_usadas:
                        # En la pasada 2 miramos toda la plata disponible, no solo el monto exacto
                        sim = calcular_similitud(nombre_inscrito, trans['Nombre_Limpio'])
                        if sim >= 60:
                            candidatos.append({
                                'index': i, 'similitud': sim, 'monto': trans['Monto'], 
                                'fecha': trans['Fecha'], 'nombre_banco': trans['Nombre_Limpio']
                            })
                            
                if candidatos:
                    candidatos.sort(key=lambda x: x['similitud'], reverse=True)
                    mejor = candidatos[0]
                    sim_str = round(mejor['similitud'])
                    
                    df_ins.at[idx, 'Similitud Nombre %'] = sim_str
                    df_ins.at[idx, 'Fecha Encontrada'] = mejor['fecha']
                    df_ins.at[idx, 'Monto Encontrado'] = mejor['monto']
                    
                    # Chequeo de empates peligrosos
                    if len(candidatos) > 1 and round(mejor['similitud']) == round(candidatos[1]['similitud']):
                         df_ins.at[idx, 'Estado Pago'] = 'Revisión manual'
                         df_ins.at[idx, 'Motivo'] = f"EMPATE PELIGROSO ({sim_str}%). Revisar a mano."
                         transferencias_usadas.add(mejor['index'])
                         continue
                         
                    # Diagnóstico
                    df_ins.at[idx, 'Estado Pago'] = 'Revisión manual'
                    
                    if not lleno_form:
                         df_ins.at[idx, 'Motivo'] = f"Despistado: Plata en banco ({sim_str}%) pero no llenó formulario."
                    elif mejor['monto'] != monto_form:
                         df_ins.at[idx, 'Motivo'] = f"Diferencia de Montos: Form={monto_form} | Banco={int(mejor['monto'])} ({sim_str}% sim)."
                    else:
                         df_ins.at[idx, 'Motivo'] = f"Baja similitud ({sim_str}%). Posible 3er pagador ('{mejor['nombre_banco']}')."
                         
                    transferencias_usadas.add(mejor['index'])
                    
                else: # No hay candidatos ni siquiera al 60%
                    if lleno_form:
                        df_ins.at[idx, 'Estado Pago'] = 'Alerta'
                        df_ins.at[idx, 'Motivo'] = "Llenó form pero no hay NADA similar en el banco."
                    else:
                        df_ins.at[idx, 'Estado Pago'] = 'No pagado'
                        df_ins.at[idx, 'Motivo'] = "Sin registro de pago ni formulario."


            # --- 5. FILTRAR COLUMNAS PARA EL EXCEL FINAL ---
            col_id_lista = [c for c in df_ins.columns if str(c).strip().lower() == 'id']
            col_carrera_lista = [c for c in df_ins.columns if str(c).strip().lower() == 'carrera']
            
            renombres = {col_nombre: 'Nombre completo', 'RUT_Limpio': 'RUT'}
            if col_id_lista: renombres[col_id_lista[0]] = 'ID'
            if col_carrera_lista: renombres[col_carrera_lista[0]] = 'Carrera'
                
            df_ins = df_ins.rename(columns=renombres)
            
            columnas_deseadas = [
                'ID', 'Nombre completo', 'RUT', 'Carrera', 'Estado Pago', 
                'Motivo', 'Similitud Nombre %', 'Monto Encontrado', 'Fecha Encontrada'
            ]
            
            for col in columnas_deseadas:
                if col not in df_ins.columns: df_ins[col] = None
                    
            df_final = df_ins[columnas_deseadas]
            sobrantes = df_cartola.drop(index=list(transferencias_usadas))

            # --- 6. PREPARAR DESCARGA ---
            st.success(f"✅ ¡Listo! Se cruzaron los {len(df_final)} inscritos.")
            st.info("Todos los resultados están en una misma hoja del Excel.")
            
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df_final.to_excel(writer, index=False, sheet_name='Todos los Inscritos')
                sobrantes.to_excel(writer, index=False, sheet_name='Sobrantes Banco')
            
            st.download_button(
                label="📥 Descargar Lista Completa", 
                data=output.getvalue(), 
                file_name="Reporte_Conciliacion_Integral.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            
        except Exception as e:
            st.error(f"Error técnico detallado: {e}")
