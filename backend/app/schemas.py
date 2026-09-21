from __future__ import annotations

import time
from typing import Optional
from pydantic import BaseModel, Field


class Ingredient(BaseModel):
    amount: Optional[float] = None
    unit: Optional[str] = None
    name: str
    note: Optional[str] = None
    group: Optional[str] = None  # e.g. "For the dough", "For the filling"
    step_index: Optional[int] = None  # 0-based index of the step that needs this ingredient


class Step(BaseModel):
    instruction: str
    title: Optional[str] = None
    time_minutes: Optional[int] = None


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, other: "TokenUsage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


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
    duplicate_match: Optional[str] = None    # name of the probably-already-existing Tandoor recipe
    duplicate_exact: bool = False            # True = (near-)exact title match, False = only similar


class Job(BaseModel):
    id: str
    filename: str
    status: str = "processing"  # processing | ready | error
    error: Optional[str] = None
    page_count: int = 0
    recipes: list[ExtractedRecipe] = Field(default_factory=list)
    images: dict[str, dict] = Field(default_factory=dict)  # image_id -> {page, path}
    suggested_cookbook_name: Optional[str] = None
    cookbook_name: Optional[str] = None  # name confirmed/edited by the user
    progress_current: int = 0
    progress_total: int = 0
    progress_label: Optional[str] = None
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    created_at: float = Field(default_factory=time.time)  # epoch seconds, used for cleanup
