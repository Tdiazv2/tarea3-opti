"""
Optimización Avanzada 2026-20 - Tarea 3
Caribean Shipping Company: programación de turnos semanales de tripulaciones
mediante Generación de Columnas (Python-Gurobi).

Estructura del script
---------------------
1. Datos (Tabla 1: requerimientos b_df, Tabla 2: costos c_df).
2. Problema Maestro Relajado (RMP) inicializado con la técnica de la gran M:
   una variable artificial por cada restricción de demanda (d, f) con costo M.
3. Problema Auxiliar (AP): genera el turno semanal (columna) de menor costo
   reducido dados los duales u_df del RMP.
4. Ciclo de generación de columnas hasta que el costo reducido mínimo >= 0.
5. Problema Maestro Entero (IMP) usando sólo las columnas generadas (literal f).
6. Reporte de resultados (consola, Excel y gráficas) y análisis de optimalidad (literal g).
"""

import os
import sys
sys.stdout.reconfigure(encoding="utf-8")  # tildes correctas en consola Windows
import math
import gurobipy as gp
from gurobipy import GRB
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # backend sin ventana: sólo se guardan las figuras
import matplotlib.pyplot as plt

# Carpeta donde se guardan las salidas (misma carpeta del script)
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultados")
os.makedirs(OUT_DIR, exist_ok=True)

# ----------------------------------------------------------------------------
# 1. DATOS
# ----------------------------------------------------------------------------
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
FRANJAS = [f"{2*k:02d}:00-{2*k+2:02d}:00" for k in range(12)]

D = range(1, 8)          # D: días de la semana (1 = lunes, ..., 7 = domingo)
F = range(1, 13)         # F: franjas de 2 horas (1 = 00:00-02:00, ..., 12 = 22:00-24:00)
I = range(1, 10)         # I: franjas en las que puede iniciar un turno de 8 h (4 franjas)
LARGO_TURNO = 4          # un turno dura exactamente 4 franjas (8 horas)
FINDE = (6, 7)           # sábado y domingo

# Si es True, la semana se considera cíclica (domingo -> lunes son consecutivos)
SEMANA_CICLICA = False

# b[d, f]: requerimiento mínimo de tripulaciones (Tabla 1). Filas = franjas, columnas = días.
_req = [
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
# c[d, f]: costo de una tripulación en la franja f del día d (Tabla 2)
_cos = [
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
b = {(d, f): _req[f - 1][d - 1] for d in D for f in F}
c = {(d, f): _cos[f - 1][d - 1] for d in D for f in F}

# Gran M: debe ser mayor que el costo de cualquier turno real.
# Un turno cuesta como máximo 5 días * 4 franjas * 130 = 2600, se usa un valor mucho mayor.
M = 1e5

# ----------------------------------------------------------------------------
# Utilidades sobre columnas
# ----------------------------------------------------------------------------
# Una columna (turno semanal) se representa como un diccionario {d: i}:
# el día d la tripulación trabaja iniciando en la franja i (si d no está, descansa).


def cubre(turno, d, f):
    """a_dfp: 1 si el turno trabaja el día d en la franja f, 0 de lo contrario."""
    return 1 if d in turno and turno[d] <= f <= turno[d] + LARGO_TURNO - 1 else 0


def costo_turno(turno):
    """c_p = sum_d sum_f c_df * a_dfp (costo semanal de una tripulación en el turno p)."""
    return sum(c[d, f] for d in D for f in F if cubre(turno, d, f))


def describir(turno):
    """Texto legible del turno, p.ej. 'Lun 06-14 | Mar 06-14 | ...'."""
    partes = []
    for d in D:
        if d in turno:
            i = turno[d]
            partes.append(f"{DIAS[d-1][:3]} {2*(i-1):02d}-{2*(i-1)+8:02d}")
        else:
            partes.append(f"{DIAS[d-1][:3]} ----")
    return " | ".join(partes)


# ----------------------------------------------------------------------------
# 2. PROBLEMA MAESTRO RELAJADO (RMP) con gran M
# ----------------------------------------------------------------------------
rmp = gp.Model("RMP")
rmp.Params.OutputFlag = 0

# Variables artificiales s_df >= 0 con costo M: forman una base inicial factible
# (matriz identidad), por lo que el RMP es factible desde la iteración 0.
s = rmp.addVars(D, F, lb=0, obj=M, name="s")

# Restricciones de demanda: sum_p a_dfp x_p + s_df >= b_df  (dual u_df >= 0)
dem = {(d, f): rmp.addConstr(s[d, f] >= b[d, f], name=f"dem[{d},{f}]") for d in D for f in F}
rmp.ModelSense = GRB.MINIMIZE

columnas = []   # lista de turnos (diccionarios {d: i}) generados
x = []          # variables x_p del RMP asociadas a cada columna


def agregar_columna(turno):
    """Agrega la columna del turno al RMP: coeficiente a_dfp en cada restricción de demanda."""
    col = gp.Column()
    for d in D:
        for f in F:
            if cubre(turno, d, f):
                col.addTerms(1.0, dem[d, f])
    p = len(columnas)
    var = rmp.addVar(lb=0, obj=costo_turno(turno), column=col, name=f"x[{p}]")
    columnas.append(turno)
    x.append(var)


# ----------------------------------------------------------------------------
# 3. PROBLEMA AUXILIAR (AP)
# ----------------------------------------------------------------------------
ap = gp.Model("AP")
ap.Params.OutputFlag = 0

# z_di = 1 si la tripulación trabaja el día d iniciando en la franja i (binaria)
z = ap.addVars(D, I, vtype=GRB.BINARY, name="z")
# w_d = 1 si la tripulación trabaja el día d (variable auxiliar binaria)
w = ap.addVars(D, vtype=GRB.BINARY, name="w")

# Un solo turno de exactamente 8 horas (4 franjas) por día trabajado
ap.addConstrs((z.sum(d, "*") == w[d] for d in D), name="un_turno_dia")
# Entre 32 y 40 horas semanales: 4 o 5 días de 8 horas
ap.addConstr(w.sum() >= 4, name="min_32h")
ap.addConstr(w.sum() <= 5, name="max_40h")
# No más de 2 días consecutivos: en cualquier ventana de 3 días se trabajan máximo 2
ventanas = range(1, 8) if SEMANA_CICLICA else range(1, 6)
ap.addConstrs((w[d] + w[(d % 7) + 1] + w[((d + 1) % 7) + 1] <= 2 for d in ventanas),
              name="max_2_consec")
# Exactamente un día del fin de semana libre (trabaja uno, no los dos)
ap.addConstr(w[6] + w[7] == 1, name="un_dia_finde")
ap.ModelSense = GRB.MINIMIZE


def resolver_ap(u):
    """Resuelve el AP con los duales u_df y retorna (costo reducido, turno).

    ca_di = sum_{f=i}^{i+3} (c_df - u_df): costo reducido de trabajar el día d iniciando en i.
    FO: min sum_d sum_i ca_di z_di  = c_q - sum_df u_df a_dfq  (costo reducido del turno q).
    """
    for d in D:
        for i in I:
            z[d, i].Obj = sum(c[d, f] - u[d, f] for f in range(i, i + LARGO_TURNO))
    ap.optimize()
    turno = {d: i for d in D for i in I if z[d, i].X > 0.5}
    return ap.ObjVal, turno


# ----------------------------------------------------------------------------
# 4. CICLO DE GENERACIÓN DE COLUMNAS
# ----------------------------------------------------------------------------
EPS = 1e-6
historial = []
it = 0
while True:
    rmp.optimize()
    u = {(d, f): dem[d, f].Pi for d in D for f in F}          # duales del RMP
    cr, turno = resolver_ap(u)                                 # pricing
    art = sum(s[d, f].X for d in D for f in F)
    historial.append({"iteracion": it, "FO_RMP": rmp.ObjVal, "costo_reducido_min": cr,
                      "suma_artificiales": art, "columnas": len(columnas)})
    print(f"It {it:3d} | FO RMP = {rmp.ObjVal:14.2f} | c_red min = {cr:12.2f} | "
          f"artificiales = {art:8.2f} | columnas = {len(columnas)}")
    if cr >= -EPS:        # criterio de parada: ningún turno tiene costo reducido negativo
        break
    agregar_columna(turno)
    it += 1

z_lp = rmp.ObjVal
art_final = sum(s[d, f].X for d in D for f in F)
sol_lp = {p: x[p].X for p in range(len(columnas)) if x[p].X > 1e-6}
duales = {(d, f): dem[d, f].Pi for d in D for f in F}

print("\n================ RESULTADOS GENERACIÓN DE COLUMNAS (literal e) ================")
print(f"Columnas generadas: {len(columnas)}   Iteraciones: {it}")
print(f"Costo total RMP (relajación lineal): {z_lp:,.2f}")
print(f"Suma de variables artificiales: {art_final:.4f} "
      f"({'OK, solución factible del problema original' if art_final < 1e-6 else 'NO FACTIBLE'})")
print(f"Tripulaciones (fraccional): {sum(sol_lp.values()):.2f}")
print("\nTurnos con x_p > 0 en la solución lineal:")
for p, v in sorted(sol_lp.items(), key=lambda t: -t[1]):
    print(f"  x[{p:3d}] = {v:8.3f}  costo = {costo_turno(columnas[p]):5d}  {describir(columnas[p])}")

# ----------------------------------------------------------------------------
# 5. PROBLEMA MAESTRO ENTERO (IMP) con las columnas generadas (literal f)
# ----------------------------------------------------------------------------
imp = gp.Model("IMP")
imp.Params.OutputFlag = 0
P = range(len(columnas))
y = imp.addVars(P, vtype=GRB.INTEGER, lb=0, name="x")
imp.setObjective(gp.quicksum(costo_turno(columnas[p]) * y[p] for p in P), GRB.MINIMIZE)
imp.addConstrs((gp.quicksum(y[p] for p in P if cubre(columnas[p], d, f)) >= b[d, f]
                for d in D for f in F), name="dem")
imp.Params.MIPGap = 0
imp.optimize()

z_ip = imp.ObjVal
sol_ip = {p: int(round(y[p].X)) for p in P if y[p].X > 0.5}
cota_inf = math.ceil(z_lp - 1e-6)   # costos enteros => ceil(z_LP) es cota inferior del entero
gap = (z_ip - z_lp) / z_lp * 100
gap_ceil = (z_ip - cota_inf) / z_ip * 100

print("\n================ RESULTADOS PROBLEMA ENTERO (literal f) ================")
print(f"Costo total IMP (columnas generadas): {z_ip:,.0f}")
print(f"Tripulaciones contratadas: {sum(sol_ip.values())}   Turnos distintos usados: {len(sol_ip)}")
for p, v in sorted(sol_ip.items(), key=lambda t: -t[1]):
    print(f"  x[{p:3d}] = {v:4d}  costo = {costo_turno(columnas[p]):5d}  {describir(columnas[p])}")

# ----------------------------------------------------------------------------
# 6. ANÁLISIS DE OPTIMALIDAD (literal g)
# ----------------------------------------------------------------------------
print("\n================ ANÁLISIS DE OPTIMALIDAD (literal g) ================")
print(f"z_LP (cota inferior del entero)      = {z_lp:,.2f}")
print(f"ceil(z_LP) (costos enteros)          = {cota_inf:,}")
print(f"z_IP con columnas generadas          = {z_ip:,.0f}")
print(f"Brecha (z_IP - z_LP)/z_LP            = {gap:.4f} %")
print(f"Brecha contra ceil(z_LP)             = {gap_ceil:.4f} %")
if z_ip <= cota_inf + 1e-6:
    print("=> El IMP alcanza la cota inferior: la solución entera es ÓPTIMA para el problema original.")
else:
    print("=> El IMP no alcanza la cota: la solución entera es factible pero no se garantiza su "
          "optimalidad (se requeriría Branch & Price).")

# Cobertura de la solución entera: equipos asignados vs. requeridos
cob = {(d, f): sum(v for p, v in sol_ip.items() if cubre(columnas[p], d, f)) for d in D for f in F}
holgura_total = sum(cob[k] - b[k] for k in cob)
print(f"Exceso total de cobertura (equipos-franja sobre el mínimo): {holgura_total}")

# ----------------------------------------------------------------------------
# 6b. ¿POR QUÉ HAY EXCESO DE COBERTURA? Mínimo de turnos necesarios por día
# ----------------------------------------------------------------------------
# Para cada día d se resuelve: min sum_i n_i  s.a. sum_{i: i<=f<=i+3} n_i >= b_df, n_i entero.
# Es el número mínimo de turnos de 8 h que necesita ese día, ignorando las reglas semanales.
min_turnos = {}
for d in D:
    m = gp.Model(f"min_turnos_{d}")
    m.Params.OutputFlag = 0
    n = m.addVars(I, vtype=GRB.INTEGER, lb=0)
    m.setObjective(n.sum(), GRB.MINIMIZE)
    m.addConstrs((gp.quicksum(n[i] for i in I if i <= f <= i + LARGO_TURNO - 1) >= b[d, f] for f in F))
    m.optimize()
    min_turnos[d] = int(round(m.ObjVal))
# Tripulaciones que trabajan cada día en la solución entera
trabajan = {d: sum(v for p, v in sol_ip.items() if d in columnas[p]) for d in D}
cota_trip = min_turnos[6] + min_turnos[7]
n_trip = sum(sol_ip.values())

print("\n---- Turnos por día: mínimo necesario vs. solución entera ----")
for d in D:
    print(f"  {DIAS[d-1]:10s} mínimo = {min_turnos[d]:4d}   asignados = {trabajan[d]:4d}   "
          f"exceso = {trabajan[d] - min_turnos[d]:4d}")
print(f"Cada tripulación trabaja EXACTAMENTE un día del fin de semana, luego:\n"
      f"  N° tripulaciones >= mín(sábado) + mín(domingo) = {min_turnos[6]} + {min_turnos[7]} = {cota_trip}"
      f"   (solución: {n_trip})")
print(f"Cada tripulación trabaja al menos 3 días entre semana => >= {3*n_trip} turnos lun-vie, "
      f"aunque lun-vie solo necesitan {sum(min_turnos[d] for d in range(1, 6))}.\n"
      f"  => El exceso entre semana es inevitable y el modelo lo ubica en las franjas más baratas "
      f"(06:00-14:00, costo 66).")

# ----------------------------------------------------------------------------
# 7. SALIDAS: Excel y gráficas
# ----------------------------------------------------------------------------
def horario(t, d):
    """Texto 'HH-HH' del turno t en el día d, o 'Libre'."""
    return f"{2*(t[d]-1):02d}-{2*(t[d]-1)+8:02d}" if d in t else "Libre"


df_hist = pd.DataFrame(historial)
df_lp = pd.DataFrame([{"columna": p, "x_LP": v, "costo_turno": costo_turno(columnas[p]),
                       **{DIAS[d-1]: horario(columnas[p], d) for d in D}}
                      for p, v in sol_lp.items()]).sort_values("x_LP", ascending=False)
df_ip = pd.DataFrame([{"columna": p, "tripulaciones": v, "costo_turno": costo_turno(columnas[p]),
                       "costo_total": v * costo_turno(columnas[p]),
                       **{DIAS[d-1]: horario(columnas[p], d) for d in D}}
                      for p, v in sol_ip.items()]).sort_values("tripulaciones", ascending=False)
df_dias = pd.DataFrame({"dia": DIAS, "min_turnos_necesarios": [min_turnos[d] for d in D],
                        "tripulaciones_asignadas": [trabajan[d] for d in D]})
df_cob = pd.DataFrame([[cob[d, f] for d in D] for f in F], index=FRANJAS, columns=DIAS)
df_req = pd.DataFrame([[b[d, f] for d in D] for f in F], index=FRANJAS, columns=DIAS)
df_dual = pd.DataFrame([[duales[d, f] for d in D] for f in F], index=FRANJAS, columns=DIAS)
df_cols = pd.DataFrame([{"columna": p, "costo_turno": costo_turno(t), "turno": describir(t)}
                        for p, t in enumerate(columnas)])
df_res = pd.DataFrame({"indicador": ["Columnas generadas", "Iteraciones", "Costo RMP (LP)",
                                     "Costo IMP (entero)", "ceil(z_LP)", "Brecha % vs z_LP",
                                     "Tripulaciones (entero)", "Cota inferior tripulaciones (fin de semana)",
                                     "Exceso de cobertura"],
                       "valor": [len(columnas), it, round(z_lp, 2), z_ip, cota_inf, round(gap, 4),
                                 n_trip, cota_trip, holgura_total]})

with pd.ExcelWriter(os.path.join(OUT_DIR, "resultados_tarea3.xlsx")) as xw:
    df_res.to_excel(xw, sheet_name="Resumen", index=False)
    df_hist.to_excel(xw, sheet_name="Iteraciones", index=False)
    df_cols.to_excel(xw, sheet_name="Columnas generadas", index=False)
    df_lp.to_excel(xw, sheet_name="Solucion LP", index=False)
    df_ip.to_excel(xw, sheet_name="Solucion entera", index=False)
    df_dias.to_excel(xw, sheet_name="Turnos por dia", index=False)
    df_req.to_excel(xw, sheet_name="Requerimiento")
    df_cob.to_excel(xw, sheet_name="Cobertura entera")
    (df_cob - df_req).to_excel(xw, sheet_name="Exceso cobertura")
    df_dual.to_excel(xw, sheet_name="Duales finales")

AZUL, ROJO, GRIS, NARANJA, VERDE = "#1f6f8b", "#c0392b", "#9aa5ab", "#e67e22", "#d5f5e3"
miles = matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}")

# Gráfica 1: convergencia. Fase gran M (artificiales > 0) y fase sin artificiales.
fase2 = df_hist[df_hist["suma_artificiales"] < 1e-6]
it_f2 = int(fase2["iteracion"].min())
fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
ax[0].plot(df_hist["iteracion"], df_hist["suma_artificiales"], marker=".", color=GRIS)
ax[0].axvline(it_f2, ls=":", color="black")
ax[0].annotate(f"Artificiales = 0\nen la iteración {it_f2}", (it_f2, 0),
               xytext=(it_f2 + 8, df_hist["suma_artificiales"].max() * 0.5),
               arrowprops=dict(arrowstyle="->"))
ax[0].set_title("Fase gran M: demanda cubierta por artificiales (Σ s_df)")
ax[0].set_xlabel("Iteración"); ax[0].set_ylabel("Equipos-franja artificiales")
ax[1].plot(fase2["iteracion"], fase2["FO_RMP"], marker=".", color=AZUL, label="FO del RMP")
ax[1].axhline(z_ip, ls="--", color=ROJO, label=f"Óptimo = {z_ip:,.0f}")
ax[1].set_title("Costo del RMP sin artificiales")
ax[1].set_xlabel("Iteración"); ax[1].set_ylabel("Costo semanal"); ax[1].legend()
ax[1].yaxis.set_major_formatter(miles)
fig.tight_layout(); fig.savefig(os.path.join(OUT_DIR, "1_convergencia.png"), dpi=150)

# Gráfica 2: requerimiento vs. cobertura por día (una gráfica por día, franjas en el eje x)
fig, axs = plt.subplots(2, 4, figsize=(15, 7), sharey=True)
for k, d in enumerate(D):
    a = axs.flat[k]
    for f in F:   # fondo verde: franjas más baratas del día (06:00-14:00)
        if c[d, f] == min(c[d, g] for g in F):
            a.axvspan(f - 0.5, f + 0.5, color=VERDE, zorder=0)
    a.bar(list(F), [b[d, f] for f in F], color=GRIS, label="Requerimiento mínimo", zorder=2)
    a.step(list(F), [cob[d, f] for f in F], where="mid", color=AZUL, lw=2,
           label="Equipos asignados", zorder=3)
    a.set_title(f"{DIAS[d-1]}  ({trabajan[d]} tripulaciones)")
    a.set_xticks(list(F)); a.set_xticklabels([f"{2*(f-1):02d}" for f in F], fontsize=7)
    a.set_xlabel("Hora de inicio de la franja", fontsize=8)
axs.flat[7].axis("off")
h1, l1 = axs.flat[0].get_legend_handles_labels()
axs.flat[7].legend(h1 + [matplotlib.patches.Patch(color=VERDE)],
                   l1 + ["Franjas más baratas (06-14)"], loc="center")
fig.suptitle("Requerimiento vs. cobertura de la solución entera", fontsize=13)
fig.tight_layout(); fig.savefig(os.path.join(OUT_DIR, "2_cobertura_por_dia.png"), dpi=150)

# Gráfica 3: turnos por día. Explica el exceso: el fin de semana fija el número de tripulaciones.
fig, ax = plt.subplots(figsize=(9, 4.8))
pos = list(range(7))
ax.bar([p - 0.2 for p in pos], [min_turnos[d] for d in D], width=0.4, color=GRIS,
       label="Mínimo de turnos que necesita el día")
ax.bar([p + 0.2 for p in pos], [trabajan[d] for d in D], width=0.4, color=AZUL,
       label="Tripulaciones asignadas ese día")
for p, d in zip(pos, D):
    ax.text(p - 0.2, min_turnos[d] + 4, str(min_turnos[d]), ha="center", fontsize=8, color="#555")
    ax.text(p + 0.2, trabajan[d] + 4, str(trabajan[d]), ha="center", fontsize=8)
ax.set_xticks(pos); ax.set_xticklabels(DIAS)
ax.set_ylabel("Turnos de 8 h")
ax.set_title(f"Sábado + domingo exigen ≥ {min_turnos[6]} + {min_turnos[7]} = {cota_trip} tripulaciones "
             f"(solución: {n_trip}).\nCada una trabaja 3-4 días entre semana ⇒ el exceso de lun-vie es inevitable",
             fontsize=10)
ax.set_ylim(0, max(trabajan.values()) * 1.25)
ax.legend(loc="upper center", ncol=2, fontsize=8)
fig.tight_layout(); fig.savefig(os.path.join(OUT_DIR, "3_turnos_por_dia.png"), dpi=150)

# Gráfica 4: estructura de las asignaciones (12 turnos más usados como horario semanal)
top = df_ip.head(12)
fig, ax = plt.subplots(figsize=(12, 5.5))
for r, (_, fila) in enumerate(top.iterrows()):
    t = columnas[fila["columna"]]
    for d in t:
        ax.broken_barh([((d - 1) * 24 + 2 * (t[d] - 1), 8)], (r - 0.35, 0.7),
                       color=NARANJA if d in FINDE else AZUL)
    ax.text(7 * 24 + 1, r, f"{fila['tripulaciones']} trip. · ${fila['costo_turno']:,}/sem",
            va="center", fontsize=8)
ax.set_yticks(range(len(top))); ax.set_yticklabels([f"Turno {q}" for q in top["columna"]])
ax.invert_yaxis()
ax.set_xticks([(d - 1) * 24 + 12 for d in D]); ax.set_xticklabels(DIAS)
for d in range(8):
    ax.axvline(d * 24, color="#ccc", lw=0.8)
ax.set_xlim(0, 7 * 24 + 26)
ax.set_title("Los 12 turnos semanales más usados en la solución entera (naranja = fin de semana)")
fig.tight_layout(); fig.savefig(os.path.join(OUT_DIR, "4_estructura_turnos.png"), dpi=150)

# Se eliminan las gráficas de versiones anteriores del script
for viejo in ("convergencia.png", "cobertura_entera.png"):
    if os.path.exists(os.path.join(OUT_DIR, viejo)):
        os.remove(os.path.join(OUT_DIR, viejo))

print(f"\nArchivos guardados en: {OUT_DIR}")
