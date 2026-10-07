"""
Dashboard du benchmark : restitution de la couche gold.

Règle : l'application lit uniquement les tables gold (main_gold.mart_*), elle ne recalcule rien.
Lancement (depuis la racine du projet) : streamlit run app/streamlit_app.py
"""
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

DB_PATH = Path("warehouse/trivia.duckdb")
COLOR_MODEL = "#2A6F97"     # barres de précision
COLOR_CHANCE = "#9AA5B1"    # score du hasard
COLOR_WRONG = "#C8553D"     # réponses fausses

st.set_page_config(page_title="Benchmark IA - Trivia", page_icon="🎯", layout="wide")


# ---------------------------------------------------------------- lecture du gold
@st.cache_data
def load(table):
    """Lit une table gold. Connexion en lecture seule pour ne pas bloquer dbt."""
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        return con.sql(f"SELECT * FROM main_gold.{table}").df()


if not DB_PATH.exists():
    st.error("Base gold introuvable : lancez d'abord `dbt run --profiles-dir .` depuis la racine du projet.")
    st.stop()

try:
    glob = load("mart_perf_global")
    cat = load("mart_perf_by_category")
    diff = load("mart_perf_by_difficulty")
    typ = load("mart_perf_by_type")
    rtime = load("mart_response_time")
    scope = load("mart_benchmark_scope")
    catdiff = load("mart_perf_by_category_difficulty")
    length = load("mart_perf_by_question_length")
    bias = load("mart_position_bias")
    errors = load("mart_error_examples")
except duckdb.IOException:
    st.error("La base est verrouillée par un autre programme (dbt ou l'interface DuckDB). Fermez-le puis rechargez la page.")
    st.stop()


# ---------------------------------------------------------------- filtres
st.sidebar.header("Filtres")
model = st.sidebar.selectbox("Modèle", sorted(glob["model"].unique()))
prompt = st.sidebar.selectbox("Version du prompt", sorted(glob.loc[glob["model"] == model, "prompt_version"].unique()))
st.sidebar.caption(
    "Lecture : une barre grise indique le score obtenu en répondant au hasard. "
    "Les traits sur les barres montrent la marge d'erreur à 95 % : "
    "si deux marges se chevauchent, l'écart n'est pas significatif."
)


def pick(df):
    return df[(df["model"] == model) & (df["prompt_version"] == prompt)].copy()


g = pick(glob).iloc[0]

# ---------------------------------------------------------------- en-tête
st.title("Benchmark d'un modèle d'IA sur 5 298 questions de culture générale")
st.write(
    f"Modèle **{model}**, prompt **{prompt}**. Questions issues d'Open Trivia DB "
    "(licence CC BY-SA 4.0), réponses générées localement avec LM Studio."
)

k1, k2, k3, k4 = st.columns(4)
k1.metric("Bonnes réponses", f"{g.precision_pct:.1f} %", f"±{g.marge_erreur_pts:.1f} pts de marge", delta_color="off")
k2.metric("Score au hasard", f"{g.hasard_pct:.1f} %", f"+{g.gain_vs_hasard_pts:.1f} pts pour le modèle")
k3.metric("Temps de réponse médian", f"{g.temps_median_s:.2f} s", f"moyenne {g.temps_moyen_s:.2f} s", delta_color="off")
k4.metric("Réponses au bon format", f"{g.reponses_lisibles_pct:.0f} %", f"{int(g.nb_questions)} questions", delta_color="off")

tab_cat, tab_diff, tab_type, tab_len, tab_bias, tab_time, tab_err, tab_scope = st.tabs(
    ["Par thème", "Par difficulté", "QCM ou vrai/faux", "Longueur des questions",
     "Biais de position", "Temps de réponse", "Exemples d'erreurs", "Périmètre"]
)


def bar_with_chance(df, label_col, horizontal=False, height=450):
    """Barres de précision avec marge d'erreur, et repère du score au hasard."""
    fig = go.Figure()
    if horizontal:
        fig.add_bar(y=df[label_col], x=df["precision_pct"], orientation="h", name="Modèle",
                    marker_color=COLOR_MODEL, error_x=dict(type="data", array=df["marge_erreur_pts"]),
                    customdata=df["nb_questions"],
                    hovertemplate="%{y} : %{x:.1f} % (%{customdata} questions)<extra></extra>")
        fig.add_scatter(y=df[label_col], x=df["hasard_pct"], mode="markers", name="Hasard",
                        marker=dict(color=COLOR_CHANCE, symbol="line-ns-open", size=14, line_width=3))
        fig.update_xaxes(title="Bonnes réponses (%)", range=[0, 100])
    else:
        fig.add_bar(x=df[label_col], y=df["precision_pct"], name="Modèle", marker_color=COLOR_MODEL,
                    error_y=dict(type="data", array=df["marge_erreur_pts"]),
                    customdata=df["nb_questions"],
                    hovertemplate="%{x} : %{y:.1f} % (%{customdata} questions)<extra></extra>")
        fig.add_bar(x=df[label_col], y=df["hasard_pct"], name="Hasard", marker_color=COLOR_CHANCE)
        fig.update_yaxes(title="Bonnes réponses (%)", range=[0, 100])
    fig.update_layout(height=height, barmode="group", legend=dict(orientation="h", y=1.08),
                      margin=dict(l=10, r=10, t=30, b=10))
    return fig


# ---------------------------------------------------------------- par thème
with tab_cat:
    st.subheader("Sur quels thèmes le modèle est-il fort ou faible ?")
    c = pick(cat)
    min_q = st.slider("Masquer les thèmes avec moins de N questions (marge trop large)", 0, 200, 0, step=10)
    c = c[c["nb_questions"] >= min_q].sort_values("precision_pct")
    st.plotly_chart(bar_with_chance(c, "category", horizontal=True, height=max(400, 26 * len(c))),
                    width="stretch")
    if len(c):
        best, worst = c.iloc[-1], c.iloc[0]
        st.info(
            f"Meilleur thème : **{best.category}** ({best.precision_pct:.1f} %). "
            f"Thème le plus faible : **{worst.category}** ({worst.precision_pct:.1f} %). "
            "Le modèle réussit mieux les connaissances encyclopédiques que la culture populaire."
        )
    st.markdown("**Quels thèmes pèsent le plus dans les erreurs ?**")
    st.caption("Un thème peut faire baisser le score global simplement parce qu'il est très représenté dans le dataset.")
    poids = c.sort_values("part_erreurs_pct", ascending=False).head(8)
    fig = go.Figure()
    fig.add_bar(y=poids["category"], x=poids["part_questions_pct"], orientation="h",
                name="Part des questions", marker_color=COLOR_CHANCE)
    fig.add_bar(y=poids["category"], x=poids["part_erreurs_pct"], orientation="h",
                name="Part des erreurs", marker_color=COLOR_WRONG)
    fig.update_layout(barmode="group", height=420, yaxis=dict(autorange="reversed"),
                      xaxis_title="% du total", legend=dict(orientation="h", y=1.08),
                      margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, width="stretch")

    st.markdown("**Carte thème x difficulté**")
    h = pick(catdiff)
    heat = h.pivot_table(index="category", columns="difficulty", values="precision_pct")
    heat = heat[[col for col in ["easy", "medium", "hard"] if col in heat.columns]]
    heat = heat.loc[heat.mean(axis=1).sort_values(ascending=False).index]
    fig = px.imshow(heat, text_auto=".0f", aspect="auto", color_continuous_scale="RdYlGn",
                    zmin=0, zmax=100, labels=dict(color="% justes", x="Difficulté", y=""))
    fig.update_layout(height=max(400, 24 * len(heat)), margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width="stretch")

    st.dataframe(c.sort_values("precision_pct", ascending=False)
                 [["category", "nb_questions", "precision_pct", "marge_erreur_pts", "hasard_pct",
                   "part_questions_pct", "part_erreurs_pct", "temps_moyen_s"]],
                 hide_index=True, width="stretch")

# ---------------------------------------------------------------- par difficulté
with tab_diff:
    st.subheader("La précision chute-t-elle avec la difficulté ?")
    d = pick(diff).sort_values("ordre_difficulte")
    st.plotly_chart(bar_with_chance(d, "difficulty"), width="stretch")
    st.caption("Si les marges d'erreur de deux niveaux se chevauchent, le modèle ne fait pas de différence réelle entre eux.")
    st.dataframe(d[["difficulty", "nb_questions", "precision_pct", "marge_erreur_pts", "hasard_pct", "temps_moyen_s"]],
                 hide_index=True, width="stretch")

# ---------------------------------------------------------------- par type
with tab_type:
    st.subheader("Le modèle réussit-il mieux les QCM ou les vrai/faux ?")
    t = pick(typ)
    t["question_type"] = t["question_type"].map({"multiple": "QCM (4 choix)", "boolean": "Vrai / faux"})
    fig = go.Figure()
    fig.add_bar(x=t["question_type"], y=t["precision_pct"], name="Modèle", marker_color=COLOR_MODEL)
    fig.add_bar(x=t["question_type"], y=t["hasard_pct"], name="Hasard", marker_color=COLOR_CHANCE)
    fig.update_layout(barmode="group", height=420, yaxis=dict(title="Bonnes réponses (%)", range=[0, 100]),
                      legend=dict(orientation="h", y=1.08), margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, width="stretch")
    cols = st.columns(len(t))
    for col, row in zip(cols, t.itertuples()):
        col.metric(f"Gain sur le hasard : {row.question_type}", f"+{row.gain_vs_hasard_pts:.1f} pts")
    st.caption("Le score brut est plus élevé en vrai/faux, mais le hasard y donne déjà 50 % : "
               "c'est le gain sur le hasard qui mesure ce que le modèle sait vraiment.")

# ---------------------------------------------------------------- longueur des questions
with tab_len:
    st.subheader("Le modèle réussit-il mieux les questions courtes ou longues ?")
    l = pick(length).sort_values("longueur")
    st.plotly_chart(bar_with_chance(l, "longueur"), width="stretch")
    st.dataframe(l[["longueur", "nb_questions", "precision_pct", "marge_erreur_pts", "hasard_pct", "temps_median_s"]],
                 hide_index=True, width="stretch")

# ---------------------------------------------------------------- biais de position
with tab_bias:
    st.subheader("Le modèle choisit-il certaines lettres par réflexe ?")
    b = pick(bias)
    if b.empty:
        st.info("Pas de position enregistrée pour ce prompt (question ouverte).")
    else:
        fig = go.Figure()
        fig.add_bar(x=b["lettre"], y=b["pct_choisie_par_modele"], name="Lettre choisie par le modèle",
                    marker_color=COLOR_MODEL)
        fig.add_bar(x=b["lettre"], y=b["pct_bonne_reponse"], name="Lettre de la bonne réponse",
                    marker_color=COLOR_CHANCE)
        fig.update_layout(barmode="group", height=420, yaxis_title="% des QCM",
                          legend=dict(orientation="h", y=1.08), margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig, width="stretch")
        st.caption("Les choix étant mélangés, la bonne réponse tombe sur chaque lettre environ 25 % du temps. "
                   "Un modèle sans biais choisirait chaque lettre à peu près aussi souvent.")

# ---------------------------------------------------------------- temps de réponse
with tab_time:
    st.subheader("Le modèle met-il plus de temps quand il se trompe ?")
    r = pick(rtime)
    r["reponse"] = r["ai_correct"].map({True: "Réponse juste", False: "Réponse fausse"})
    long = r.melt(id_vars="reponse", value_vars=["temps_median_s", "temps_p90_s", "temps_max_s"],
                  var_name="mesure", value_name="secondes")
    long["mesure"] = long["mesure"].map({"temps_median_s": "Médiane", "temps_p90_s": "90 % des réponses en moins de",
                                         "temps_max_s": "Maximum"})
    fig = px.bar(long, x="mesure", y="secondes", color="reponse", barmode="group",
                 color_discrete_map={"Réponse juste": COLOR_MODEL, "Réponse fausse": COLOR_WRONG})
    fig.update_layout(height=420, legend=dict(orientation="h", y=1.08, title=None),
                      xaxis_title=None, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, width="stretch")
    st.caption("La médiane résiste aux réponses exceptionnellement lentes ; le 90e centile montre justement ces cas lents.")
    st.dataframe(r[["reponse", "nb_questions", "temps_median_s", "temps_moyen_s", "temps_p90_s", "temps_max_s"]],
                 hide_index=True, width="stretch")

# ---------------------------------------------------------------- exemples d'erreurs
with tab_err:
    st.subheader("À quoi ressemblent concrètement les erreurs du modèle ?")
    e = pick(errors)
    theme = st.selectbox("Thème", sorted(e["category"].unique()))
    st.dataframe(e[e["category"] == theme][["difficulty", "question", "correct_answer", "reponse_du_modele"]],
                 hide_index=True, width="stretch")

# ---------------------------------------------------------------- périmètre
with tab_scope:
    st.subheader("Sur quoi porte ce benchmark ?")
    s = scope.iloc[0]
    a, b, c3 = st.columns(3)
    a.metric("Questions du corpus", int(s.nb_questions_corpus))
    b.metric("Questions testées", int(s.nb_questions_testees), f"{s.couverture_pct:.0f} % de couverture", delta_color="off")
    c3.metric("Réponses générées", int(s.nb_reponses), f"{int(s.nb_modeles)} modèle(s), {int(s.nb_prompts)} prompt(s)",
              delta_color="off")
    st.markdown(
        "- **Source** : Open Trivia DB, 5 298 questions vérifiées, 24 catégories, 3 difficultés.\n"
        "- **Modèle** : exécuté localement avec LM Studio, température 0 (réponses reproductibles).\n"
        "- **Format** : QCM, choix mélangés dans un ordre fixe par question pour éviter le biais de position.\n"
        "- **Pipeline** : bronze (CSV) → silver (Parquet) → gold (DuckDB, dbt) → ce dashboard."
    )