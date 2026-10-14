# Tarea 3 - Optimizacion Avanzada 2026-20
# Caribean Shipping Company: turnos semanales de tripulaciones con generacion de columnas.
#
# Orden del codigo:
#   1. Datos (tablas 1 y 2 del enunciado)
#   2. Problema maestro relajado, arranca con variables artificiales (gran M)
#   3. Problema auxiliar, busca el turno con menor costo reducido
#   4. Ciclo de generacion de columnas (literal e)
#   5. Problema entero con las columnas generadas (literal f)
#   6. Optimalidad (literal g) y analisis del exceso de cobertura
#   7. Excel y graficas

import os
import sys
import math
import gurobipy as gp
from gurobipy import GRB
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.stdout.reconfigure(encoding="utf-8")  # para que las tildes salgan bien en la consola de Windows

CARPETA_RESULTADOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultados")
os.makedirs(CARPETA_RESULTADOS, exist_ok=True)


# =============================================================================
# 1. DATOS
# =============================================================================
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
NOMBRES_FRANJAS = [f"{2*k:02d}:00-{2*k+2:02d}:00" for k in range(12)]

D = range(1, 8)    # dias, 1 = lunes ... 7 = domingo
F = range(1, 13)   # franjas de 2 horas, 1 = 00-02 ... 12 = 22-24
I = range(1, 10)   # franjas donde puede empezar un turno (el turno no pasa de medianoche)
FRANJAS_POR_TURNO = 4   # 8 horas = 4 franjas
SABADO, DOMINGO = 6, 7

# Si se pone en True, domingo y lunes cuentan como dias seguidos
SEMANA_CICLICA = False

# Tabla 1: requerimiento minimo (filas = franjas, columnas = dias)
tabla_requerimiento = [
    [32, 80, 45, 17, 30, 82, 45],
    [35, 61, 40, 84, 40, 45, 49],
    [39, 79, 31, 42, 25, 69, 42],
    [48, 59, 46, 87, 28, 59, 84],
    [53, 16, 16, 90, 25, 34, 78],
    [34, 24, 36, 87, 25, 73, 59],
    [25, 49, 24, 89, 75, 89, 79],
    [44, 22, 27, 38, 49, 38, 29],
    [30, 27, 61, 63, 69, 46, 71],
    [21, 74, 35, 20, 90, 43, 72],
    [63, 29, 26, 79, 84, 57, 79],
    [49, 47, 61, 32, 33, 90, 76],
]
# Tabla 2: costo de una tripulacion por franja
tabla_costos = [
    [100, 100, 100, 100, 100, 100, 130],
    [100, 100, 100, 100, 100, 100, 130],
    [100, 100, 100, 100, 100, 100, 130],
    [66, 66, 66, 66, 66, 66, 84],
    [66, 66, 66, 66, 66, 66, 84],
    [66, 66, 66, 66, 66, 66, 84],
    [66, 66, 66, 66, 66, 66, 84],
    [72, 72, 72, 72, 72, 72, 97],
    [72, 72, 72, 72, 72, 72, 97],
    [100, 100, 100, 100, 100, 100, 130],
    [100, 100, 100, 100, 100, 100, 130],
    [100, 100, 100, 100, 100, 100, 130],
]
b = {(d, f): tabla_requerimiento[f - 1][d - 1] for d in D for f in F}   # b_df
c = {(d, f): tabla_costos[f - 1][d - 1] for d in D for f in F}          # c_df

# Gran M. El turno mas caro posible cuesta 5 * 4 * 130 = 2600, entonces 100000 alcanza de sobra
M = 1e5


# =============================================================================
# Funciones sobre los turnos
# =============================================================================
# Un turno (columna) se guarda como diccionario {dia: franja de inicio}.
# Si un dia no aparece, ese dia la tripulacion descansa.

def trabaja_en_franja(turno, d, f):
    """Devuelve a_dfp: 1 si el turno cubre la franja f del dia d."""
    if d in turno and turno[d] <= f <= turno[d] + FRANJAS_POR_TURNO - 1:
        return 1
    return 0


def calcular_costo_turno(turno):
    """Calcula c_p sumando el costo de cada franja que cubre el turno."""
    return sum(c[d, f] for d in D for f in F if trabaja_en_franja(turno, d, f))


def formatear_horario(turno, d):
    """Devuelve el horario del dia d como 'HH-HH', o 'Libre'."""
    if d not in turno:
        return "Libre"
    hora_inicio = 2 * (turno[d] - 1)
    return f"{hora_inicio:02d}-{hora_inicio + 8:02d}"


def describir_turno(turno):
    """Arma un texto corto con el horario de toda la semana."""
    return " | ".join(f"{DIAS[d-1][:3]} {formatear_horario(turno, d)}" for d in D)


# =============================================================================
# 2. PROBLEMA MAESTRO RELAJADO
# =============================================================================
maestro = gp.Model("maestro_relajado")
maestro.Params.OutputFlag = 0

# Artificiales s_df con costo M. Forman una base identidad, asi que el maestro
# es factible desde el inicio aunque todavia no tenga ningun turno.
artificial = maestro.addVars(D, F, lb=0, obj=M, name="s")

# Demanda: sum_p a_dfp x_p + s_df >= b_df. Sus duales son los u_df
restr_demanda = {}
for d in D:
    for f in F:
        restr_demanda[d, f] = maestro.addConstr(artificial[d, f] >= b[d, f], name=f"demanda[{d},{f}]")
maestro.ModelSense = GRB.MINIMIZE

turnos_generados = []   # columnas que se van agregando
x = []                  # variable x_p de cada columna


def agregar_columna(turno):
    """Mete el turno al maestro con coeficiente 1 en las franjas que cubre."""
    columna = gp.Column()
    for d in D:
        for f in F:
            if trabaja_en_franja(turno, d, f):
                columna.addTerms(1.0, restr_demanda[d, f])
    p = len(turnos_generados)
    x.append(maestro.addVar(lb=0, obj=calcular_costo_turno(turno), column=columna, name=f"x[{p}]"))
    turnos_generados.append(turno)


# =============================================================================
# 3. PROBLEMA AUXILIAR
# =============================================================================
auxiliar = gp.Model("auxiliar")
auxiliar.Params.OutputFlag = 0

z = auxiliar.addVars(D, I, vtype=GRB.BINARY, name="z")   # z_di = 1 si trabaja el dia d empezando en i
w = auxiliar.addVars(D, vtype=GRB.BINARY, name="w")      # w_d = 1 si trabaja el dia d

# Si trabaja ese dia, hace un solo turno de 8 horas
auxiliar.addConstrs((z.sum(d, "*") == w[d] for d in D), name="un_turno_por_dia")
# Entre 32 y 40 horas a la semana, o sea 4 o 5 dias
auxiliar.addConstr(w.sum() >= 4, name="min_32_horas")
auxiliar.addConstr(w.sum() <= 5, name="max_40_horas")
# Maximo 2 dias seguidos: en cada ventana de 3 dias trabaja a lo sumo 2
dias_inicio_ventana = range(1, 8) if SEMANA_CICLICA else range(1, 6)
auxiliar.addConstrs((w[d] + w[d % 7 + 1] + w[(d + 1) % 7 + 1] <= 2 for d in dias_inicio_ventana),
                    name="max_2_seguidos")
# Trabaja sabado o domingo, pero no los dos
auxiliar.addConstr(w[SABADO] + w[DOMINGO] == 1, name="un_dia_fin_de_semana")
auxiliar.ModelSense = GRB.MINIMIZE


def resolver_auxiliar(u):
    """Actualiza la FO con los duales y devuelve (costo reducido, turno nuevo).

    El coeficiente de z_di es ca_di = sum_{f=i}^{i+3} (c_df - u_df), entonces el valor
    objetivo es justo el costo reducido c_q - sum u_df a_dfq del turno que sale.
    """
    for d in D:
        for i in I:
            z[d, i].Obj = sum(c[d, f] - u[d, f] for f in range(i, i + FRANJAS_POR_TURNO))
    auxiliar.optimize()
    turno_nuevo = {d: i for d in D for i in I if z[d, i].X > 0.5}
    return auxiliar.ObjVal, turno_nuevo


# =============================================================================
# 4. GENERACION DE COLUMNAS (literal e)
# =============================================================================
TOLERANCIA = 1e-6
historial = []
iteracion = 0
while True:
    maestro.optimize()
    duales = {(d, f): restr_demanda[d, f].Pi for d in D for f in F}
    costo_reducido, turno_nuevo = resolver_auxiliar(duales)
    suma_artificiales = sum(artificial[d, f].X for d in D for f in F)

    historial.append({"iteracion": iteracion, "FO_maestro": maestro.ObjVal,
                      "costo_reducido_min": costo_reducido,
                      "suma_artificiales": suma_artificiales, "columnas": len(turnos_generados)})
    print(f"It {iteracion:3d}  FO maestro = {maestro.ObjVal:14.2f}  c. reducido = {costo_reducido:12.2f}"
          f"  artificiales = {suma_artificiales:8.2f}  columnas = {len(turnos_generados)}")

    # Parada: si ningun turno tiene costo reducido negativo, ya no hay nada que mejore
    if costo_reducido >= -TOLERANCIA:
        break
    agregar_columna(turno_nuevo)
    iteracion += 1

costo_relajado = maestro.ObjVal
artificiales_final = sum(artificial[d, f].X for d in D for f in F)
solucion_relajada = {p: x[p].X for p in range(len(turnos_generados)) if x[p].X > 1e-6}

print("\n--- Resultados generacion de columnas (literal e) ---")
print(f"Columnas generadas: {len(turnos_generados)}   Iteraciones: {iteracion}")
print(f"Costo del maestro relajado: {costo_relajado:,.2f}")
if artificiales_final < 1e-6:
    print("Las artificiales quedaron en 0, la solucion es factible para el problema original")
else:
    print(f"OJO: quedan artificiales en la base ({artificiales_final:.2f}), el problema seria infactible")
print(f"Tripulaciones (fraccional): {sum(solucion_relajada.values()):.2f}")
print("Turnos usados en la solucion relajada:")
for p, valor in sorted(solucion_relajada.items(), key=lambda par: -par[1]):
    print(f"  x[{p:3d}] = {valor:8.3f}  costo = {calcular_costo_turno(turnos_generados[p]):5d}"
          f"  {describir_turno(turnos_generados[p])}")


# =============================================================================
# 5. PROBLEMA ENTERO CON LAS COLUMNAS GENERADAS (literal f)
# =============================================================================
P = range(len(turnos_generados))
entero = gp.Model("maestro_entero")
entero.Params.OutputFlag = 0
entero.Params.MIPGap = 0
x_entero = entero.addVars(P, vtype=GRB.INTEGER, lb=0, name="x")
entero.setObjective(gp.quicksum(calcular_costo_turno(turnos_generados[p]) * x_entero[p] for p in P),
                    GRB.MINIMIZE)
entero.addConstrs((gp.quicksum(x_entero[p] for p in P if trabaja_en_franja(turnos_generados[p], d, f))
                   >= b[d, f] for d in D for f in F), name="demanda")
entero.optimize()

costo_entero = entero.ObjVal
solucion_entera = {p: int(round(x_entero[p].X)) for p in P if x_entero[p].X > 0.5}
total_tripulaciones = sum(solucion_entera.values())

print("\n--- Resultados problema entero (literal f) ---")
print(f"Costo total: {costo_entero:,.0f}")
print(f"Tripulaciones: {total_tripulaciones}   Turnos distintos: {len(solucion_entera)}")
for p, cantidad in sorted(solucion_entera.items(), key=lambda par: -par[1]):
    print(f"  x[{p:3d}] = {cantidad:4d}  costo = {calcular_costo_turno(turnos_generados[p]):5d}"
          f"  {describir_turno(turnos_generados[p])}")


# =============================================================================
# 6. OPTIMALIDAD (literal g) Y EXCESO DE COBERTURA
# =============================================================================
# El relajado es cota inferior del entero. Como los costos son enteros, se puede redondear hacia arriba.
cota_inferior = math.ceil(costo_relajado - 1e-6)
brecha = (costo_entero - costo_relajado) / costo_relajado * 100

print("\n--- Optimalidad (literal g) ---")
print(f"Costo relajado (cota inferior): {costo_relajado:,.2f}")
print(f"Cota redondeada hacia arriba:   {cota_inferior:,}")
print(f"Costo entero:                   {costo_entero:,.0f}")
print(f"Brecha:                         {brecha:.4f} %")
if costo_entero <= cota_inferior + 1e-6:
    print("El entero llega a la cota, entonces es optimo para el problema original.")
else:
    print("El entero no llega a la cota: es factible pero no se puede asegurar que sea optimo "
          "(haria falta branch and price).")

# Cuantos equipos quedan en cada franja con la solucion entera
cobertura = {(d, f): sum(n for p, n in solucion_entera.items() if trabaja_en_franja(turnos_generados[p], d, f))
             for d in D for f in F}
exceso_total = sum(cobertura[k] - b[k] for k in cobertura)
print(f"Exceso de cobertura (equipos-franja por encima del minimo): {exceso_total}")


def calcular_minimo_turnos_dia(d):
    """Minimo de turnos de 8 h que necesita el dia d mirandolo solo (sin reglas semanales)."""
    modelo = gp.Model(f"minimo_dia_{d}")
    modelo.Params.OutputFlag = 0
    n = modelo.addVars(I, vtype=GRB.INTEGER, lb=0)
    modelo.setObjective(n.sum(), GRB.MINIMIZE)
    modelo.addConstrs((gp.quicksum(n[i] for i in I if i <= f <= i + FRANJAS_POR_TURNO - 1) >= b[d, f]
                       for f in F))
    modelo.optimize()
    return int(round(modelo.ObjVal))


# Esto explica de donde sale el exceso: el fin de semana obliga a tener muchas tripulaciones
minimo_por_dia = {d: calcular_minimo_turnos_dia(d) for d in D}
trabajan_por_dia = {d: sum(n for p, n in solucion_entera.items() if d in turnos_generados[p]) for d in D}
cota_tripulaciones = minimo_por_dia[SABADO] + minimo_por_dia[DOMINGO]

print("\nTurnos por dia (minimo que necesita el dia vs. asignados):")
for d in D:
    print(f"  {DIAS[d-1]:10s} minimo = {minimo_por_dia[d]:4d}  asignados = {trabajan_por_dia[d]:4d}"
          f"  sobran = {trabajan_por_dia[d] - minimo_por_dia[d]:4d}")
print(f"Cada tripulacion trabaja un solo dia del fin de semana, entonces se necesitan al menos "
      f"{minimo_por_dia[SABADO]} + {minimo_por_dia[DOMINGO]} = {cota_tripulaciones} (la solucion usa "
      f"{total_tripulaciones}).")
print(f"Cada una trabaja minimo 3 dias entre semana, o sea {3 * total_tripulaciones} turnos de lunes a "
      f"viernes, cuando esos dias solo piden {sum(minimo_por_dia[d] for d in range(1, 6))}. "
      f"Lo que sobra se pone en las franjas de 06 a 14, que son las mas baratas.")


# =============================================================================
# 7. EXCEL Y GRAFICAS
# =============================================================================
AZUL, ROJO, GRIS, NARANJA, VERDE = "#1f6f8b", "#c0392b", "#9aa5ab", "#e67e22", "#d5f5e3"


def construir_tabla_turnos(solucion, nombre_valor):
    """Arma una tabla con los turnos usados y su horario por dia."""
    filas = []
    for p, valor in solucion.items():
        fila = {"columna": p, nombre_valor: valor, "costo_turno": calcular_costo_turno(turnos_generados[p])}
        for d in D:
            fila[DIAS[d-1]] = formatear_horario(turnos_generados[p], d)
        filas.append(fila)
    return pd.DataFrame(filas).sort_values(nombre_valor, ascending=False)


def construir_tabla_franjas(valores):
    """Pasa un diccionario {(d, f): valor} a tabla franjas x dias."""
    return pd.DataFrame([[valores[d, f] for d in D] for f in F], index=NOMBRES_FRANJAS, columns=DIAS)


def exportar_excel():
    tabla_resumen = pd.DataFrame({
        "indicador": ["Columnas generadas", "Iteraciones", "Costo relajado", "Costo entero",
                      "Cota inferior redondeada", "Brecha %", "Tripulaciones",
                      "Minimo de tripulaciones por fin de semana", "Exceso de cobertura"],
        "valor": [len(turnos_generados), iteracion, round(costo_relajado, 2), costo_entero,
                  cota_inferior, round(brecha, 4), total_tripulaciones, cota_tripulaciones, exceso_total]})
    tabla_entera = construir_tabla_turnos(solucion_entera, "tripulaciones")
    tabla_entera["costo_total"] = tabla_entera["tripulaciones"] * tabla_entera["costo_turno"]
    tabla_dias = pd.DataFrame({"dia": DIAS,
                               "minimo_turnos": [minimo_por_dia[d] for d in D],
                               "tripulaciones_asignadas": [trabajan_por_dia[d] for d in D]})
    tabla_columnas = pd.DataFrame([{"columna": p, "costo_turno": calcular_costo_turno(t),
                                    "turno": describir_turno(t)} for p, t in enumerate(turnos_generados)])

    with pd.ExcelWriter(os.path.join(CARPETA_RESULTADOS, "resultados_tarea3.xlsx")) as archivo:
        tabla_resumen.to_excel(archivo, sheet_name="Resumen", index=False)
        pd.DataFrame(historial).to_excel(archivo, sheet_name="Iteraciones", index=False)
        tabla_columnas.to_excel(archivo, sheet_name="Columnas generadas", index=False)
        construir_tabla_turnos(solucion_relajada, "x_relajado").to_excel(archivo, sheet_name="Solucion relajada", index=False)
        tabla_entera.to_excel(archivo, sheet_name="Solucion entera", index=False)
        tabla_dias.to_excel(archivo, sheet_name="Turnos por dia", index=False)
        construir_tabla_franjas(b).to_excel(archivo, sheet_name="Requerimiento")
        construir_tabla_franjas(cobertura).to_excel(archivo, sheet_name="Cobertura entera")
        construir_tabla_franjas({k: cobertura[k] - b[k] for k in b}).to_excel(archivo, sheet_name="Exceso cobertura")
        construir_tabla_franjas(duales).to_excel(archivo, sheet_name="Duales finales")
    return tabla_entera


def graficar_convergencia():
    datos = pd.DataFrame(historial)
    sin_artificiales = datos[datos["suma_artificiales"] < 1e-6]
    primera_sin_art = int(sin_artificiales["iteracion"].min())

    fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
    ax[0].plot(datos["iteracion"], datos["suma_artificiales"], marker=".", color=GRIS)
    ax[0].axvline(primera_sin_art, ls=":", color="black")
    ax[0].annotate(f"Artificiales = 0\nen la iteracion {primera_sin_art}", (primera_sin_art, 0),
                   xytext=(primera_sin_art + 8, datos["suma_artificiales"].max() * 0.5),
                   arrowprops=dict(arrowstyle="->"))
    ax[0].set_title("Fase gran M: demanda cubierta por artificiales")
    ax[0].set_xlabel("Iteracion")
    ax[0].set_ylabel("Suma de s_df")

    ax[1].plot(sin_artificiales["iteracion"], sin_artificiales["FO_maestro"], marker=".", color=AZUL,
               label="FO maestro relajado")
    ax[1].axhline(costo_entero, ls="--", color=ROJO, label=f"Optimo = {costo_entero:,.0f}")
    ax[1].set_title("Costo del maestro sin artificiales")
    ax[1].set_xlabel("Iteracion")
    ax[1].set_ylabel("Costo semanal")
    ax[1].yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax[1].legend()
    fig.tight_layout()
    fig.savefig(os.path.join(CARPETA_RESULTADOS, "1_convergencia.png"), dpi=150)


def graficar_cobertura_por_dia():
    fig, ejes = plt.subplots(2, 4, figsize=(15, 7), sharey=True)
    for k, d in enumerate(D):
        eje = ejes.flat[k]
        costo_minimo = min(c[d, g] for g in F)
        for f in F:  # sombrear las franjas mas baratas del dia
            if c[d, f] == costo_minimo:
                eje.axvspan(f - 0.5, f + 0.5, color=VERDE, zorder=0)
        eje.bar(list(F), [b[d, f] for f in F], color=GRIS, label="Requerimiento minimo", zorder=2)
        eje.step(list(F), [cobertura[d, f] for f in F], where="mid", color=AZUL, lw=2,
                 label="Equipos asignados", zorder=3)
        eje.set_title(f"{DIAS[d-1]} ({trabajan_por_dia[d]} tripulaciones)")
        eje.set_xticks(list(F))
        eje.set_xticklabels([f"{2*(f-1):02d}" for f in F], fontsize=7)
        eje.set_xlabel("Hora de inicio de la franja", fontsize=8)
    ejes.flat[7].axis("off")
    manejadores, etiquetas = ejes.flat[0].get_legend_handles_labels()
    ejes.flat[7].legend(manejadores + [matplotlib.patches.Patch(color=VERDE)],
                        etiquetas + ["Franjas mas baratas (06-14)"], loc="center")
    fig.suptitle("Requerimiento vs. cobertura de la solucion entera", fontsize=13)
    fig.tight_layout()
    fig.savefig(os.path.join(CARPETA_RESULTADOS, "2_cobertura_por_dia.png"), dpi=150)


def graficar_turnos_por_dia():
    fig, ax = plt.subplots(figsize=(9, 4.8))
    posiciones = list(range(7))
    ax.bar([p - 0.2 for p in posiciones], [minimo_por_dia[d] for d in D], width=0.4, color=GRIS,
           label="Minimo de turnos que necesita el dia")
    ax.bar([p + 0.2 for p in posiciones], [trabajan_por_dia[d] for d in D], width=0.4, color=AZUL,
           label="Tripulaciones asignadas ese dia")
    for p, d in zip(posiciones, D):
        ax.text(p - 0.2, minimo_por_dia[d] + 4, str(minimo_por_dia[d]), ha="center", fontsize=8, color="#555")
        ax.text(p + 0.2, trabajan_por_dia[d] + 4, str(trabajan_por_dia[d]), ha="center", fontsize=8)
    ax.set_xticks(posiciones)
    ax.set_xticklabels(DIAS)
    ax.set_ylabel("Turnos de 8 h")
    ax.set_title(f"Sabado + domingo piden al menos {minimo_por_dia[SABADO]} + {minimo_por_dia[DOMINGO]} = "
                 f"{cota_tripulaciones} tripulaciones (solucion: {total_tripulaciones}).\n"
                 f"Como cada una trabaja 3 o 4 dias entre semana, de lunes a viernes sobran turnos",
                 fontsize=10)
    ax.set_ylim(0, max(trabajan_por_dia.values()) * 1.25)
    ax.legend(loc="upper center", ncol=2, fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(CARPETA_RESULTADOS, "3_turnos_por_dia.png"), dpi=150)


def graficar_estructura_turnos(tabla_entera, cuantos=12):
    mas_usados = tabla_entera.head(cuantos)
    fig, ax = plt.subplots(figsize=(12, 5.5))
    for fila_num, (_, fila) in enumerate(mas_usados.iterrows()):
        turno = turnos_generados[fila["columna"]]
        for d in turno:
            inicio_hora = (d - 1) * 24 + 2 * (turno[d] - 1)
            color = NARANJA if d in (SABADO, DOMINGO) else AZUL
            ax.broken_barh([(inicio_hora, 8)], (fila_num - 0.35, 0.7), color=color)
        ax.text(7 * 24 + 1, fila_num, f"{fila['tripulaciones']} trip. - ${fila['costo_turno']:,}/sem",
                va="center", fontsize=8)
    ax.set_yticks(range(len(mas_usados)))
    ax.set_yticklabels([f"Turno {p}" for p in mas_usados["columna"]])
    ax.invert_yaxis()
    ax.set_xticks([(d - 1) * 24 + 12 for d in D])
    ax.set_xticklabels(DIAS)
    for d in range(8):
        ax.axvline(d * 24, color="#ccc", lw=0.8)
    ax.set_xlim(0, 7 * 24 + 26)
    ax.set_title(f"Los {cuantos} turnos mas usados en la solucion entera (naranja = fin de semana)")
    fig.tight_layout()
    fig.savefig(os.path.join(CARPETA_RESULTADOS, "4_estructura_turnos.png"), dpi=150)


tabla_entera = exportar_excel()
graficar_convergencia()
graficar_cobertura_por_dia()
graficar_turnos_por_dia()
graficar_estructura_turnos(tabla_entera)

print(f"\nResultados guardados en {CARPETA_RESULTADOS}")
