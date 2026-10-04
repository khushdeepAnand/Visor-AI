"""Router for the layouts domain (extracted from api/main.py)."""

from fastapi import APIRouter
from api.deps import *  # noqa: F401,F403


router = APIRouter()



@router.get("/api/v1/workspaces/{workspace}")
def workspace_get_endpoint(workspace: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    conn = get_connection()
    row = conn.execute("SELECT layout_json,updated_at FROM user_workspace_layouts WHERE user_id=? AND workspace=?", (user["id"], workspace[:80])).fetchone()
    conn.close()
    return {"workspace": workspace, "layout": json.loads(row[0]) if row else {}, "updated_at": row[1] if row else None}


@router.put("/api/v1/workspaces/{workspace}")
def workspace_put_endpoint(workspace: str, payload: WorkspacePayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    conn = get_connection()
    conn.execute(
        """INSERT INTO user_workspace_layouts(user_id,workspace,layout_json,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(user_id,workspace) DO UPDATE SET layout_json=excluded.layout_json,updated_at=CURRENT_TIMESTAMP""",
        (user["id"], workspace[:80], json.dumps(payload.layout)),
    )
    conn.commit(); conn.close()
    return {"saved": True}


@router.post("/api/v1/chart-layouts", status_code=201)
def chart_layouts_create_endpoint(
    payload: ChartLayoutPayload, user: dict[str, Any] = Depends(current_user)
) -> dict[str, Any]:
    _require_feature("saved_chart_layouts")
    try:
        return _serializable(
            CHART_LAYOUTS.save_layout(
                user_id=user["id"],
                name=payload.name,
                symbol=payload.symbol,
                timeframe=payload.timeframe,
                overlays=payload.overlays,
                visible_range=payload.visible_range,
            )
        )
    except ChartLayoutError as exc:
        raise _chart_layout_error(exc) from exc


@router.get("/api/v1/chart-layouts")
def chart_layouts_list_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    _require_feature("saved_chart_layouts")
    return _serializable({"items": CHART_LAYOUTS.list_layouts(user_id=user["id"])})


@router.get("/api/v1/chart-layouts/{layout_id}")
def chart_layouts_get_endpoint(layout_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    _require_feature("saved_chart_layouts")
    try:
        return _serializable(CHART_LAYOUTS.get_layout(user_id=user["id"], layout_id=layout_id))
    except ChartLayoutError as exc:
        raise _chart_layout_error(exc) from exc


@router.delete("/api/v1/chart-layouts/{layout_id}")
def chart_layouts_delete_endpoint(layout_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    _require_feature("saved_chart_layouts")
    try:
        return _serializable(CHART_LAYOUTS.delete_layout(user_id=user["id"], layout_id=layout_id))
    except ChartLayoutError as exc:
        raise _chart_layout_error(exc) from exc
