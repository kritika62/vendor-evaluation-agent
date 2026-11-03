# Deploying the dashboard (Streamlit Community Cloud, free)

> This guide covers the Streamlit dashboard only. The FastAPI + React app is meant to run locally
> (no login, one evaluation at a time); see README.md.

## 0. Security first
- Keep your API key in a local `.env` file. It is listed in `.gitignore`; never commit it.
- Confirm with `git status` before every commit.

## 1. Push to GitHub
    git init
    git add .
    git commit -m "Vendor evaluation dashboard"
    git branch -M main
    git remote add origin https://github.com/<you>/<repo>.git
    git push -u origin main
Make sure the `vea/` folder is included (app.py imports `vea.dashboard_data`).
Add an empty `vea/__init__.py` if it is missing.

## 2. Deploy
1. Go to share.streamlit.io and sign in with GitHub.
2. New app -> pick your repo, branch `main`, main file `app.py`.
3. Deploy. Python deps come from `requirements.txt` (dashboard only, so builds are fast).

## 3. Using it
The cloud app cannot see your local `output/` folder, so users upload `scorecard.xlsx`
in the sidebar. To show a demo instead, commit a sample workbook at `demo/scorecard.xlsx`
(non-confidential!) and change the default path in the sidebar to it.

## Confidentiality
Anything uploaded goes to a public cloud host. For real company proposals, deploy
inside company infrastructure (Docker/VM) or keep the app private (Streamlit Cloud
"Share" settings -> restrict viewers) and check with your mentor.

## Running the agent
The scoring agent (main.py) needs heavy ML packages and an API key, so run it locally:
    pip install -r requirements-agent.txt
    python main.py --rfp data/rfp/RFP_Parts_Tracking.txt

For the FastAPI backend as well: `pip install -r requirements-api.txt` (includes the agent packages).
