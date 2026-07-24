import streamlit as st
import pandas as pd
import unicodedata
from io import BytesIO
from rapidfuzz import fuzz

st.set_page_config(page_title="Conciliación Cartola Ombligo", layout="centered")

# Funciones extraídas de tus módulos originales
def limpiar_texto_bancario(texto):
    if not isinstance(texto, str): return ""
    texto = unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('utf-8')
    texto = texto.upper().replace("TRASPASO DE:", "").replace("TRASPASO DE :", "")
    return " ".join(texto.split())

def calcular_similitud(nombre_inscrito, nombre_banco):
    n_inscrito = str(nombre_inscrito)
    n_banco = str(nombre_banco)
    return max(fuzz.token_set_ratio(n_inscrito, n_banco), fuzz.partial_ratio(n_inscrito, n_banco))

st.title("Conciliación por Cartola (Motor Inteligente)")
st.markdown("Busca transferencias usando similitud de nombres y montos exactos.")

# Tu configuración interactiva de montos
montos_input = st.text_input("Montos válidos para el mes, separados por coma (Ej: 17500, 19500)", "17500, 19500")

file_cartola = st.file_uploader("1. Sube la Cartola Bancaria (Excel)", type=['xlsx', 'xls', 'csv'])
file_inscripciones = st.file_uploader("2. Sube la Base de Inscripciones (Excel)", type=['xlsx'])

if st.button("Conciliar Pagos") and file_cartola and file_inscripciones:
    with st.spinner("Analizando transferencias con IA..."):
        try:
            montos_aceptados = [int(m.strip()) for m in montos_input.split(',')]
            
            # --- LEER CARTOLA (Buscando dónde empiezan los datos) ---
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
            
            # Filtrar solo ingresos
            df_cartola = df_cartola.dropna(subset=[col_abonos])
            df_cartola = df_cartola[df_cartola[col_abonos] > 0].copy()
            df_cartola['Nombre_Limpio'] = df_cartola[col_desc].apply(limpiar_texto_bancario)
            df_cartola = df_cartola.rename(columns={col_fecha: 'Fecha', col_abonos: 'Monto', col_desc: 'Descripcion'})
            
            # --- LEER INSCRIPCIONES ---
            df_ins = pd.read_excel(file_inscripciones)
            cols_nombre_completo = [c for c in df_ins.columns if 'nombre completo' in str(c).lower()]
            col_nombre = cols_nombre_completo[0] if cols_nombre_completo else [c for c in df_ins.columns if 'nombre' in str(c).lower()][0]
            df_ins['Nombre_Limpio'] = df_ins[col_nombre].apply(limpiar_texto_bancario)
            
            # --- MOTOR DE CONCILIACIÓN ---
            df_ins['Estado_Pago'] = 'No pagado'
            df_ins['Similitud_%'] = 0.0
            df_ins['Fecha_Pago'] = None
            
            transferencias_usadas = set()
            
            for idx, row in df_ins.iterrows():
                nombre_inscrito = row['Nombre_Limpio']
                candidatos = []
                
                for i, trans in df_cartola.iterrows():
                    if i not in transferencias_usadas and trans['Monto'] in montos_aceptados:
                        similitud = calcular_similitud(nombre_inscrito, trans['Nombre_Limpio'])
                        candidatos.append({
                            'index': i, 'similitud': similitud, 'monto': trans['Monto'], 'fecha': trans['Fecha']
                        })
                
                if candidatos:
                    # Ordenar por el mejor match
                    candidatos.sort(key=lambda x: x['similitud'], reverse=True)
                    mejor = candidatos[0]
                    
                    if mejor['similitud'] >= 85: # Umbral de confianza
                        df_ins.at[idx, 'Estado_Pago'] = 'Pagado verificado'
                        df_ins.at[idx, 'Similitud_%'] = round(mejor['similitud'])
                        df_ins.at[idx, 'Fecha_Pago'] = mejor['fecha']
                        transferencias_usadas.add(mejor['index'])
            
            # --- SEPARAR RESULTADOS ---
            pagados = df_ins[df_ins['Estado_Pago'] == 'Pagado verificado']
            pendientes = df_ins[df_ins['Estado_Pago'] == 'No pagado']
            sobrantes = df_cartola.drop(index=list(transferencias_usadas))
            
            st.success(f"✅ ¡Se verificaron {len(pagados)} pagos automáticamente!")
            st.warning(f"⚠️ Faltan {len(pendientes)} personas por pagar.")
            st.info(f"❓ Quedan {len(sobrantes)} transferencias en la cartola que no calzan con nadie (Terceros pagadores o depósitos externos).")
            
            # --- DESCARGA DE REPORTE ---
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                pagados.to_excel(writer, index=False, sheet_name='Pagados Auto')
                sobrantes.to_excel(writer, index=False, sheet_name='Revisión Manual (Sobrantes)')
                pendientes.to_excel(writer, index=False, sheet_name='Pendientes')
            
            st.download_button(
                label="Descargar Reporte Completo de Conciliación", 
                data=output.getvalue(), 
                file_name="Conciliacion_Cartola_Final.xlsx"
            )
            
        except Exception as e:
            st.error(f"Error técnico detallado: {e}")
