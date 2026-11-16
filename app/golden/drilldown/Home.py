"""Golden-eval drill-down explorer -- entry point / landing page. The two
real pages (Candidates & Job Posts, Skills) live in pages/, Streamlit's
own multipage convention (auto-discovered from this script's sibling
`pages/` directory).

An internal tool for people with DB access to explore a golden-eval run
top-to-bottom -- the same decomposition tree app/golden/
build_evaluation_tree.py renders to a static HTML file, but live, with
run-switching, click-to-drill, and full per-record detail at the leaf
level (the static report stops at aggregate counts/averages).

Run with: docker compose up -d golden-drilldown, then open
http://localhost:8502

Named Home.py, not app.py: found live (a real `ModuleNotFoundError: 'app'
is not a package`) -- Streamlit puts the entry script's own directory on
sys.path, so a script literally named app.py shadows this project's
top-level `app` package the moment anything does `from app.db import
...`, turning every `app.*` import into "no submodules, it's not a
package." AppTest (which this project's tests use to verify this app)
didn't catch it because it imports the script as a module directly,
bypassing Streamlit's own sys.path setup entirely -- a real gap between
that test and an actual `streamlit run`, now known, not just guessed at.
"""
import streamlit as st

from app.db import SessionLocal
from app.golden.drilldown.data import list_runs, select_run_in_sidebar

st.set_page_config(page_title="Golden Eval Drill-Down", layout="wide")
st.title("Golden Evaluation Drill-Down")

st.markdown(
    "Pick a run in the sidebar, then open **Candidates & Job Posts** or **Skills** there. "
    "Both pages share whichever run you pick on any page."
)

session = SessionLocal()
try:
    select_run_in_sidebar(session)
    st.dataframe(list_runs(session), use_container_width=True, hide_index=True)
finally:
    session.close()
