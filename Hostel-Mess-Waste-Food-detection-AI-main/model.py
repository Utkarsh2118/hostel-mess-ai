from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score


DATASET_COLUMNS = [
	"students_present",
	"prepared_kg",
	"consumed_kg",
	"waste_kg",
	"is_weekend",
	"is_exam_period",
]


def ensure_dataset_exists(dataset_path: str | Path) -> None:
	"""Create an empty dataset file with the expected schema when missing."""
	path = Path(dataset_path)
	if not path.exists():
		pd.DataFrame(columns=DATASET_COLUMNS).to_csv(path, index=False)


def load_dataset(dataset_path: str | Path) -> pd.DataFrame:
	"""Load and sanitize dataset values used by the dashboard and ML model."""
	ensure_dataset_exists(dataset_path)
	df = pd.read_csv(dataset_path)

	if df.empty:
		return pd.DataFrame(columns=DATASET_COLUMNS)

	for col in DATASET_COLUMNS:
		if col not in df.columns:
			df[col] = 0

	numeric_cols = [
		"students_present",
		"prepared_kg",
		"consumed_kg",
		"waste_kg",
		"is_weekend",
		"is_exam_period",
	]
	for col in numeric_cols:
		df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

	return df[DATASET_COLUMNS]


def train_waste_model(df: pd.DataFrame) -> dict[str, Any]:
	"""Train linear regression on waste data and return model metadata."""
	if df.empty or len(df) < 2:
		return {
			"trained": False,
			"model": None,
			"r2": 0.0,
		}

	x = df[["students_present", "is_weekend", "is_exam_period"]]
	y = df["waste_kg"]

	model = LinearRegression()
	model.fit(x, y)
	prediction = model.predict(x)
	score = float(r2_score(y, prediction)) if len(df) > 1 else 0.0

	return {
		"trained": True,
		"model": model,
		"r2": round(score, 4),
	}


def predict_waste(
	trained_model: LinearRegression | None,
	students_present: int,
	is_weekend: int,
	is_exam_period: int,
) -> float:
	"""Predict waste (kg) with a trained model, returning a non-negative value."""
	if trained_model is None:
		return 0.0

	values = pd.DataFrame(
		[
			{
				"students_present": students_present,
				"is_weekend": is_weekend,
				"is_exam_period": is_exam_period,
			}
		]
	)
	predicted = float(trained_model.predict(values)[0])
	return round(max(predicted, 0.0), 2)


def suggested_food_quantity(df: pd.DataFrame, students_present: int) -> float:
	"""Estimate required food by using average historical consumption per student."""
	if df.empty:
		return round(students_present * 0.55, 2)

	safe_students = df["students_present"].replace(0, pd.NA)
	per_student = (df["consumed_kg"] / safe_students).dropna()

	avg_per_student = float(per_student.mean()) if not per_student.empty else 0.55
	with_buffer = avg_per_student * students_present * 1.08
	return round(max(with_buffer, 0.0), 2)
