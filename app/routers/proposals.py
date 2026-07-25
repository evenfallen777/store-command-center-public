"""proposals routes."""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Form, UploadFile, File
from deps import *
from services import *
import proposal_gate

router = APIRouter()


class ProposalCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    source: Optional[str] = "manual"
    source_label: Optional[str] = "Manual"
    tags: Optional[str] = "T-Shirt"

@router.get("/api/proposals")
def list_proposals(status: str = "pending"):
    conn = get_conn()
    # Judged proposals first, best score on top; unscored ones follow (newest first)
    rows = conn.execute(
        "SELECT * FROM proposals WHERE status=? "
        "ORDER BY (score IS NULL), score DESC, created_at DESC", (status,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.post("/api/proposals")
def create_proposal(p: ProposalCreate):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO proposals (title,description,source,source_label,tags) VALUES (?,?,?,?,?)",
        (p.title, p.description, p.source, p.source_label, p.tags)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM proposals WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return dict(row)

class ApproveProposalRequest(BaseModel):
    prompt:       Optional[str] = None
    product_type: str = "T-Shirt"
    variations:   int = 2

@router.patch("/api/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: int, background_tasks: BackgroundTasks,
                     req: ApproveProposalRequest = None,
                     variations: int = 2):
    # Accept JSON body or fall back to legacy query-param style
    if req is None:
        req = ApproveProposalRequest(variations=variations)
    conn = get_conn()
    row = conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Proposal not found")
    model = _resolve_model(conn, None)
    # Do NOT change proposal status — keep it 'pending' so it stays visible and
    # the user can generate more images or skip it later. Only 'reject' removes it.
    use_prompt = req.prompt or row["title"]
    gen_ids = []
    for _ in range(req.variations):
        cur = conn.execute(
            "INSERT INTO generations (proposal_id,prompt,product_type,model) VALUES (?,?,?,?)",
            (proposal_id, use_prompt, req.product_type, model)
        )
        gen_ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    for gid in gen_ids:
        background_tasks.add_task(run_generation, gid)
    return {"ok": True, "queued": len(gen_ids)}

@router.post("/api/proposals/{proposal_id}/enhance-prompt")
def enhance_proposal_prompt(proposal_id: int):
    """Auto-enhance a proposal's title+description into a vivid SD prompt.
    Returns {task_id} for polling via /api/tasks/{task_id}."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Proposal not found")
    concept = f"{row['title']}\n\n{row['description'] or ''}".strip()
    def _work():
        # QAT reasoning model needs ample tokens (uses ~500+ for internal reasoning before writing)
        enhanced = _call_lmstudio(_get_enhance_system(), concept, max_tokens=2000)
        return {"enhanced": enhanced, "original": concept, "proposal_id": proposal_id}
    tid = orch.submit_llm(
        _work,
        desc=f"Enhance proposal {proposal_id}: {row['title'][:40]}",
        retry_meta={"type": "enhance", "proposal_id": proposal_id, "prompt": concept},
        task="image_enhance",
    )
    return {"task_id": tid, "proposal_id": proposal_id}

@router.post("/api/proposals/{proposal_id}/review")
def review_proposal(proposal_id: int):
    """LLM-judge ONE proposal: score 0-100 + verdict (reject|hold|approve), then
    the gate acts per proposal_gate_mode (see proposal_gate.py).
    Returns {task_id} for polling via /api/task/{task_id}."""
    conn = get_conn()
    row = conn.execute("SELECT id FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Proposal not found")
    tid = proposal_gate.submit_review(proposal_id)
    return {"task_id": tid, "proposal_id": proposal_id}

@router.post("/api/proposals/review-all")
def review_all_proposals():
    """Batch-judge every pending, unscored proposal in ONE background LLM task.
    Returns {task_id, queued} ({queued: 0} when nothing needs review, or
    already_running: true when a batch is still in flight)."""
    return proposal_gate.submit_review_all(priority=1)

@router.patch("/api/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int):
    conn = get_conn()
    conn.execute("UPDATE proposals SET status='rejected',updated_at=datetime('now') WHERE id=?", (proposal_id,))
    conn.commit()
    conn.close()
    return {"ok": True}
