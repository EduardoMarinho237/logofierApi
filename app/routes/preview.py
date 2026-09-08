from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response

from app.deps import get_current_user
from app.services.pdf_processor import render_page_preview

router = APIRouter()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_preview(
    pdf: UploadFile = File(...),
    page: int = 0,
    user: object = Depends(get_current_user),
):
    if not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are allowed",
        )

    pdf_bytes = await pdf.read()
    try:
        png_bytes, width, height = render_page_preview(pdf_bytes, page)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not render page",
        )

    headers = {
        "X-Page-Width-Points": str(width),
        "X-Page-Height-Points": str(height),
    }
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers=headers,
    )
