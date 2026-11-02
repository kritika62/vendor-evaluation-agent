"""FastAPI backend for the vendor evaluation agent.

Run from the project folder (the one that contains main.py):
    uvicorn api.main:app --port 8000
Then open http://localhost:8000/docs to try every endpoint in the browser.

This file only maps HTTP to api/service.py, where the logic lives. The LLM key stays on the server (.env).
"""
import os
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import service


@asynccontextmanager
async def lifespan(app):
    service.recover()
    yield


app = FastAPI(title="Vendor Evaluation API", version="1.0",
              description="Upload an RFP and vendor proposals, run an evaluation, then read the ranking, evidence, "
                          "overrides, downloads and chat for it.", lifespan=lifespan)
app.add_middleware(CORSMiddleware,
                   allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")],
                   allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(service.ServiceError)
async def service_error(request: Request, exc: service.ServiceError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.message})


# ---------------------------------------------------------------- request bodies
class StartEvaluation(BaseModel):
    mock: bool = Field(False, description="Offline test mode: no API key, meaningless scores.")
    samples: int = Field(1, ge=1, le=5, description="LLM runs per criterion (median; flags disagreement).")
    per_file: bool = Field(False, description="Treat every file as its own vendor.")
    targets: dict[str, float] = Field(default_factory=dict,
                                      description='Numeric targets that replace the RFP, e.g. {"T6": 99.9, "T7": 6}.')


class OverrideBody(BaseModel):
    vendor: str
    score: float = Field(ge=0, le=5)
    reason: str = Field("", max_length=500)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=40)


class CriteriaUpdate(BaseModel):
    criteria: list[dict] = Field(default_factory=list,
                                 description='Changes by id, e.g. [{"id": "T4", "weight": 0.2, "mandatory": true}].')
    sub_criteria: Optional[list[dict]] = Field(None, description="The COMPLETE new sub-criteria list; omit to leave unchanged.")


# ---------------------------------------------------------------- health
@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------- RFP
@app.post("/rfp", tags=["inputs"], summary="Upload the RFP (replaces the previous one)")
def upload_rfp(file: UploadFile = File(...)):
    return service.save_rfp(file.filename, file.file)


@app.get("/rfp", tags=["inputs"])
def get_rfp():
    return service.rfp_info() or JSONResponse(status_code=404, content={"detail": "No RFP uploaded."})


@app.delete("/rfp", tags=["inputs"], status_code=204)
def delete_rfp():
    service.delete_rfp()


# ---------------------------------------------------------------- vendors and files
@app.post("/vendors/{name}/files", tags=["inputs"], summary="Add one or more files (any format) to a vendor")
def upload_vendor_files(name: str, files: list[UploadFile] = File(...)):
    return service.save_vendor_files(name, [(f.filename, f.file) for f in files])


@app.post("/vendors/{name}/file", tags=["inputs"],
          summary="Add ONE file to a vendor (shows a file picker in /docs; call again for more files)")
def upload_vendor_file(name: str, file: UploadFile = File(...)):
    return service.save_vendor_files(name, [(file.filename, file.file)])


@app.get("/vendors", tags=["inputs"], summary="Vendors and files waiting for the next evaluation")
def list_vendors():
    return service.list_vendors()


@app.delete("/vendors/{name}", tags=["inputs"], status_code=204)
def delete_vendor(name: str):
    service.delete_vendor(name)


@app.delete("/vendors/{name}/files/{filename}", tags=["inputs"], status_code=204)
def delete_vendor_file(name: str, filename: str):
    service.delete_vendor_file(name, filename)


# ---------------------------------------------------------------- evaluations
@app.post("/evaluations", tags=["evaluations"], status_code=202,
          summary="Start an evaluation of everything uploaded (runs in the background)")
def start_evaluation(body: StartEvaluation = StartEvaluation()):
    return service.start_evaluation(body.mock, body.samples, body.per_file, body.targets)


@app.get("/evaluations", tags=["evaluations"])
def list_evaluations():
    return service.list_evaluations()


@app.get("/evaluations/{eval_id}", tags=["evaluations"], summary="Status and progress (poll this)")
def evaluation_status(eval_id: str):
    return service.get_status(eval_id)


@app.get("/evaluations/{eval_id}/ranking", tags=["results"],
         summary="Ranking and category scores; optional what-if settings")
def ranking(eval_id: str, min_score: Optional[float] = Query(None, ge=0, le=5), shortlist_n: Optional[int] = Query(None, ge=1)):
    return service.get_ranking(eval_id, min_score, shortlist_n)


@app.get("/evaluations/{eval_id}/vendors/{name}", tags=["results"],
         summary="One vendor: scores, sub-scores, evidence quotes, sources, summary")
def vendor_detail(eval_id: str, name: str):
    return service.get_vendor(eval_id, name)


@app.patch("/evaluations/{eval_id}/scores/{item_id}", tags=["results"],
           summary="Override a criterion or sub-criterion score (e.g. T4 or T4.2)")
def override_score(eval_id: str, item_id: str, body: OverrideBody):
    return service.override_score(eval_id, item_id, body.vendor, body.score, body.reason)


@app.get("/evaluations/{eval_id}/overrides", tags=["results"])
def overrides(eval_id: str):
    return service.get_overrides(eval_id)


@app.get("/evaluations/{eval_id}/download", tags=["results"], summary="Download: pdf, docx, csv, json, md or xlsx")
def download(eval_id: str, format: str = "pdf", min_score: Optional[float] = Query(None, ge=0, le=5),
             shortlist_n: Optional[int] = Query(None, ge=1)):
    data, filename, mime = service.download(eval_id, format, min_score, shortlist_n)
    return Response(content=data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.post("/evaluations/{eval_id}/chat", tags=["results"], summary="Ask a question about this evaluation")
def chat(eval_id: str, body: ChatBody):
    return service.chat_about(eval_id, body.question, [m.model_dump() for m in body.history])


# ---------------------------------------------------------------- criteria
@app.get("/criteria", tags=["criteria"], summary="Criteria and sub-criteria used for new evaluations")
def get_criteria():
    return service.get_criteria()


@app.put("/criteria", tags=["criteria"], summary="Edit criteria fields and/or replace the sub-criteria list")
def update_criteria(body: CriteriaUpdate):
    return service.update_criteria(body.criteria, body.sub_criteria)
