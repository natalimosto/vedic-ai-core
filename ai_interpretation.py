from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class BirthData(BaseModel):
    date: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    time: Optional[str] = Field(default=None, description="HH:MM[:SS]")
    timezone: Optional[str] = Field(default=None, description="e.g. +05:30")
    location: Optional[str] = Field(default=None, description="Lat,Lon or place name")


class PlanetPosition(BaseModel):
    name: str
    longitude: Optional[float] = Field(default=None, description="Nirayana longitude in degrees")
    sign: Optional[str] = None
    house: Optional[int] = None
    nakshatra: Optional[str] = None
    pada: Optional[int] = None


class HousePosition(BaseModel):
    number: int
    sign: Optional[str] = None
    lord: Optional[str] = None
    longitude: Optional[float] = Field(default=None, description="House cusp longitude in degrees")


class Aspect(BaseModel):
    source: str
    target: str
    type: Optional[str] = Field(default=None, description="Conjunction, trine, etc.")
    orb: Optional[float] = None


class NatalChartInput(BaseModel):
    birth: Optional[BirthData] = None
    ayanamsa: Optional[str] = None
    planets: List[PlanetPosition] = Field(default_factory=list)
    houses: List[HousePosition] = Field(default_factory=list)
    aspects: List[Aspect] = Field(default_factory=list)
    focus_areas: List[str] = Field(default_factory=list, description="User requested topics")
    questions: List[str] = Field(default_factory=list, description="Explicit questions to answer")
    interpretation_style: Optional[str] = Field(
        default=None,
        description="Tone or lens for interpretation (e.g. deep psychological, symbolic).",
    )
    required_outputs: List[str] = Field(
        default_factory=list,
        description="Mandatory sections the interpretation must include.",
    )
    notes: Optional[str] = None


DEFAULT_REQUIRED_OUTPUTS = [
    "karmic analysis",
    "psychological dynamics",
    "unconscious projections",
    "transformation cycles",
    "financial patterns",
    "relationship patterns",
]


def _format_planet_line(planet: PlanetPosition) -> str:
    parts = [planet.name]
    if planet.sign:
        parts.append(f"in {planet.sign}")
    if planet.house is not None:
        parts.append(f"house {planet.house}")
    if planet.nakshatra:
        parts.append(f"nakshatra {planet.nakshatra}")
    if planet.pada is not None:
        parts.append(f"pada {planet.pada}")
    if planet.longitude is not None:
        parts.append(f"{planet.longitude:.4f}°")
    return " - " + ", ".join(parts)


def _find_planet(planets: List[PlanetPosition], name: str) -> Optional[PlanetPosition]:
    for planet in planets:
        if planet.name.lower() == name.lower():
            return planet
    return None


def build_prompt_from_chart(chart: NatalChartInput) -> str:
    sections: List[str] = []

    if chart.birth:
        sections.append(
            "Birth data:\n"
            f"- date: {chart.birth.date or 'unknown'}\n"
            f"- time: {chart.birth.time or 'unknown'}\n"
            f"- timezone: {chart.birth.timezone or 'unknown'}\n"
            f"- location: {chart.birth.location or 'unknown'}"
        )

    if chart.ayanamsa:
        sections.append(f"Ayanamsa: {chart.ayanamsa}")

    if chart.planets:
        planet_lines = "\n".join(_format_planet_line(p) for p in chart.planets)
        sections.append(f"Planets:\n{planet_lines}")

    if chart.houses:
        house_lines = "\n".join(
            f"- house {h.number}: sign {h.sign or 'unknown'}, lord {h.lord or 'unknown'}"
            for h in chart.houses
        )
        sections.append(f"Houses:\n{house_lines}")

    if chart.aspects:
        aspect_lines = "\n".join(
            f"- {a.source} {a.type or 'aspect'} {a.target} (orb {a.orb if a.orb is not None else 'n/a'})"
            for a in chart.aspects
        )
        sections.append(f"Aspects:\n{aspect_lines}")

    if chart.focus_areas:
        focus = "\n".join(f"- {area}" for area in chart.focus_areas)
        sections.append(f"Focus areas:\n{focus}")

    if chart.questions:
        questions = "\n".join(f"- {q}" for q in chart.questions)
        sections.append(f"Questions:\n{questions}")

    style = chart.interpretation_style or "deep psychological and symbolic; avoid generic astrology."
    required_outputs = chart.required_outputs or DEFAULT_REQUIRED_OUTPUTS
    sections.append(f"Interpretation style: {style}")
    sections.append(
        "Required outputs:\n" + "\n".join(f"- {item}" for item in required_outputs)
    )

    if chart.notes:
        sections.append(f"Notes:\n{chart.notes}")

    prompt_body = "\n\n".join(sections) if sections else "No chart data provided."
    return (
        "You are a Vedic astrology analyst. Provide a grounded, compassionate interpretation "
        "with practical guidance. Prioritize the user's questions and focus areas.\n\n"
        f"{prompt_body}\n\n"
        "Return sections that explicitly address the required outputs, then include: "
        "(1) core themes, (2) strengths, (3) challenges, (4) timing cues, "
        "(5) suggested practices."
    )


def interpret_chart(chart: NatalChartInput) -> dict:
    prompt = build_prompt_from_chart(chart)

    sun = _find_planet(chart.planets, "Sun")
    moon = _find_planet(chart.planets, "Moon")
    asc = _find_planet(chart.planets, "Asc") or _find_planet(chart.planets, "Ascendant")

    interpretation_lines = [
        "Core themes from the natal chart are derived from the luminaries and chart angles."
    ]

    if sun:
        interpretation_lines.append(
            f"Sun emphasis: identity and life purpose are colored by {sun.sign or 'its sign'}"
            + (f" in house {sun.house}." if sun.house is not None else ".")
        )
    if moon:
        interpretation_lines.append(
            f"Moon emphasis: emotional needs center on {moon.sign or 'its sign'}"
            + (f" in house {moon.house}." if moon.house is not None else ".")
        )
    if asc:
        interpretation_lines.append(
            f"Ascendant: approach to life is expressed through {asc.sign or 'its sign'}"
            + (f" in house {asc.house}." if asc.house is not None else ".")
        )

    if chart.focus_areas:
        interpretation_lines.append("Focus areas noted: " + ", ".join(chart.focus_areas) + ".")
    if chart.questions:
        interpretation_lines.append("Questions to address: " + "; ".join(chart.questions) + ".")

    if chart.required_outputs or chart.interpretation_style:
        interpretation_lines.append("This interpretation follows the requested style and outputs.")

    if len(interpretation_lines) == 1:
        interpretation_lines.append(
            "Add planet positions, houses, and aspects for a richer interpretation."
        )

    return {
        "prompt": prompt,
        "interpretation": " ".join(interpretation_lines),
    }
