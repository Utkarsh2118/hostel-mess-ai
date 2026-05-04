from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score


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
			"rmse": 0.0,
			"mae": 0.0,
			"sample_size": 0,
			"waste_std": 0.0,
		}

	x = df[["students_present", "is_weekend", "is_exam_period"]]
	y = df["waste_kg"]

	model = LinearRegression()
	model.fit(x, y)
	prediction = model.predict(x)
	score = float(r2_score(y, prediction)) if len(df) > 1 else 0.0
	rmse = float(math.sqrt(mean_squared_error(y, prediction)))
	mae = float(mean_absolute_error(y, prediction))

	return {
		"trained": True,
		"model": model,
		"r2": round(score, 4),
		"rmse": round(rmse, 3),
		"mae": round(mae, 3),
		"sample_size": len(df),
		"waste_std": round(float(y.std()), 3),
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


def prediction_confidence_interval(
	trained: dict[str, Any],
	predicted_waste: float,
	students_present: int,
) -> dict[str, float]:
	"""
	Compute a simple ±1-sigma confidence interval for the prediction.
	Wider RMSE and fewer samples → wider interval.
	"""
	rmse = float(trained.get("rmse", 0.0))
	sample_size = int(trained.get("sample_size", 0))
	r2 = float(trained.get("r2", 0.0))

	# Scale factor: small samples → larger uncertainty
	if sample_size >= 30:
		scale = 1.0
	elif sample_size >= 10:
		scale = 1.5
	elif sample_size >= 5:
		scale = 2.0
	else:
		scale = 3.0

	# Interval half-width
	half_width = round(rmse * scale, 2)
	low = round(max(predicted_waste - half_width, 0.0), 2)
	high = round(predicted_waste + half_width, 2)

	# Confidence score: blends R² with sample-size reliability
	if sample_size == 0:
		confidence_pct = 0
	else:
		size_bonus = min(1.0, sample_size / 30.0)
		confidence_pct = round(max(0.0, min(100.0, r2 * 70 + size_bonus * 30)), 1)

	# Human-readable interpretation
	if confidence_pct >= 80:
		interpretation = "High – model well-calibrated with sufficient data"
	elif confidence_pct >= 55:
		interpretation = "Moderate – more historical data will improve accuracy"
	elif confidence_pct >= 30:
		interpretation = "Low – limited data, treat as rough estimate"
	else:
		interpretation = "Very low – insufficient data for reliable prediction"

	return {
		"low": low,
		"high": high,
		"confidence_pct": confidence_pct,
		"interpretation": interpretation,
	}


def find_similar_scenarios(
	df: pd.DataFrame,
	students_present: int,
	is_weekend: int,
	is_exam_period: int,
	top_n: int = 3,
) -> list[dict]:
	"""
	Return the top-N historical records most similar to the given inputs.
	Similarity is based on student count closeness and matching boolean flags.
	"""
	if df.empty:
		return []

	# Filter by matching flags first, fall back to all records if none match
	filtered = df[
		(df["is_weekend"] == is_weekend) & (df["is_exam_period"] == is_exam_period)
	].copy()
	if filtered.empty:
		filtered = df.copy()

	filtered["_dist"] = (filtered["students_present"] - students_present).abs()
	filtered = filtered.sort_values("_dist").head(top_n)
	result = []
	for _, row in filtered.iterrows():
		result.append(
			{
				"students_present": int(row["students_present"]),
				"waste_kg": float(row["waste_kg"]),
				"prepared_kg": float(row["prepared_kg"]),
				"consumed_kg": float(row["consumed_kg"]),
				"is_weekend": int(row["is_weekend"]),
				"is_exam_period": int(row["is_exam_period"]),
			}
		)
	return result


def out_of_range_warnings(
	df: pd.DataFrame,
	predicted_waste: float,
	suggested_food: float,
	students_present: int,
) -> list[str]:
	"""Return warning strings when inputs or predictions fall outside historical ranges."""
	warnings: list[str] = []
	if df.empty:
		warnings.append("No historical data – prediction relies on defaults only.")
		return warnings

	stu_min, stu_max = int(df["students_present"].min()), int(df["students_present"].max())
	waste_min, waste_max = float(df["waste_kg"].min()), float(df["waste_kg"].max())

	if students_present < stu_min:
		warnings.append(
			f"Student count ({students_present}) is below the historical minimum ({stu_min}). Extrapolation may be less accurate."
		)
	elif students_present > stu_max:
		warnings.append(
			f"Student count ({students_present}) exceeds the historical maximum ({stu_max}). Extrapolation may be less accurate."
		)

	if predicted_waste > waste_max * 1.25:
		warnings.append(
			f"Predicted waste ({predicted_waste} kg) is significantly above historical max ({waste_max} kg)."
		)
	elif predicted_waste < waste_min * 0.75 and predicted_waste > 0:
		warnings.append(
			f"Predicted waste ({predicted_waste} kg) is significantly below historical min ({waste_min} kg)."
		)

	return warnings
