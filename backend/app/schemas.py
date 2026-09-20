from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class Ingredient(BaseModel):
    amount: Optional[float] = None
    unit: Optional[str] = None
    name: str
    note: Optional[str] = None
    group: Optional[str] = None  # z.B. "Für den Teig", "Für die Füllung"


class Step(BaseModel):
    instruction: str
    title: Optional[str] = None
    time_minutes: Optional[int] = None


class ExtractedRecipe(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    servings: Optional[int] = None
    prep_time_minutes: Optional[int] = None
    cook_time_minutes: Optional[int] = None
    total_time_minutes: Optional[int] = None
    tags: list[str] = Field(default_factory=list)
    ingredients: list[Ingredient] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    source_page_start: int
    source_page_end: int
    candidate_image_ids: list[str] = Field(default_factory=list)
    selected_image_id: Optional[str] = None
    selected: bool = True
    import_status: str = "pending"  # pending | importing | imported | error
    import_error: Optional[str] = None
    tandoor_recipe_id: Optional[int] = None
    duplicate_match: Optional[str] = None    # Name des vermutlich bereits existierenden Tandoor-Rezepts
    duplicate_exact: bool = False            # True = (fast) exakter Titel-Treffer, False = nur aehnlich


class Job(BaseModel):
    id: str
    filename: str
    status: str = "processing"  # processing | ready | error
    error: Optional[str] = None
    page_count: int = 0
    recipes: list[ExtractedRecipe] = Field(default_factory=list)
    images: dict[str, dict] = Field(default_factory=dict)  # image_id -> {page, path}
    suggested_cookbook_name: Optional[str] = None
    cookbook_name: Optional[str] = None  # vom Nutzer bestätigter/geänderter Name
    progress_current: int = 0
    progress_total: int = 0
    progress_label: Optional[str] = None
