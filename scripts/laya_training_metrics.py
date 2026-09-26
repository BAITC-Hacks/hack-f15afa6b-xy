"""Metrics, calibration and checkpoint selection for Pulse109 Laya training."""

import math
from collections import defaultdict

import numpy as np
import torch

from laya.common import temp_bucket


def fit_temperature(rows, minimum=10):
    if len(rows) < minimum:
        return 1.0
    width = max(len(row["logits"]) for row in rows)
    logits = torch.full((len(rows), width), -1e4)
    targets = torch.zeros((len(rows), width))
    for index, row in enumerate(rows):
        count = len(row["logits"])
        logits[index, :count] = torch.tensor(row["logits"])
        targets[index, :count] = torch.tensor(row["target"])
    log_temperature = torch.zeros(1, requires_grad=True)
    optimizer = torch.optim.LBFGS([log_temperature], lr=.1, max_iter=100)

    def closure():
        optimizer.zero_grad()
        loss = -(targets * torch.log_softmax(logits / log_temperature.exp(), -1)).sum(-1).mean()
        loss.backward()
        return loss

    optimizer.step(closure)
    value = float(torch.clamp(log_temperature.exp(), .5, 5).item())
    return round(value, 6) if math.isfinite(value) else 1.0


def temperature_map(rows):
    by_type = defaultdict(list)
    by_bucket = defaultdict(list)
    by_decision = defaultdict(list)
    by_language = defaultdict(list)
    for row in rows:
        by_type[row["qtype_name"]].append(row)
        bucket = temp_bucket(row["qtype"], len(row["logits"]))
        by_bucket[bucket].append(row)
        by_decision[f"{row['qid']}:{row['language']}"].append(row)
        by_language[(row["language"], bucket)].append(row)
    return {
        "by_type": {key: fit_temperature(value) for key, value in sorted(by_type.items())},
        "by_option_count": {key: fit_temperature(value) for key, value in sorted(by_bucket.items())},
        "by_decision_language": {key: fit_temperature(value) for key, value in sorted(by_decision.items())},
        "by_language_option_count": {
            language: {bucket: fit_temperature(value)
                       for (lang, bucket), value in sorted(by_language.items()) if lang == language}
            for language in sorted({key[0] for key in by_language})
        },
    }


def row_temperature(row, calibration, mode):
    if mode == "pulse":
        return calibration["by_decision_language"].get(f"{row['qid']}:{row['language']}", 1.0)
    if mode == "checkpoint":
        return calibration["by_option_count"].get(temp_bucket(row["qtype"], len(row["logits"])), 1.0)
    return 1.0


def classification_metrics(pairs, labels):
    confusion = {actual: {predicted: 0 for predicted in labels} for actual in labels}
    for actual, predicted in pairs:
        confusion[actual][predicted] += 1
    classes = {}
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in labels if other != label)
        fn = sum(confusion[label][other] for other in labels if other != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        classes[label] = {"precision": round(precision, 6), "recall": round(recall, 6),
                          "f1": round(f1, 6), "support": sum(confusion[label].values())}
    supported = [value for value in classes.values() if value["support"]]

    def mean(field):
        return round(float(np.mean([value[field] for value in supported])), 6) if supported else None

    return {
        "count": len(pairs),
        "accuracy": round(sum(actual == predicted for actual, predicted in pairs) / len(pairs), 6),
        "macro_precision": mean("precision"), "macro_recall": mean("recall"),
        "macro_f1": mean("f1"), "classes": classes, "confusion": confusion,
    }


def evaluate(rows, calibration=None, mode="raw"):
    correct, confidences, briers, losses = [], [], [], []
    category_pairs = []
    per_task, task_labels = defaultdict(list), defaultdict(set)
    per_slice, slice_labels = defaultdict(list), defaultdict(set)
    for row in rows:
        temperature = row_temperature(row, calibration or {}, mode)
        logits = np.asarray(row["logits"], dtype=float) / temperature
        probabilities = np.exp(logits - logits.max())
        probabilities /= probabilities.sum()
        target = np.asarray(row["target"], dtype=float)
        predicted, actual = int(probabilities.argmax()), int(target.argmax())
        correct.append(int(predicted == actual))
        confidences.append(float(probabilities[predicted]))
        briers.append(float(np.square(probabilities - target).sum()))
        losses.append(float(-(target * np.log(np.clip(probabilities, 1e-12, 1))).sum()))
        slice_name = f"{row['qid']}:{row['language']}"
        actual_name, predicted_name = row["keys"][actual], row["keys"][predicted]
        per_task[row["qid"]].append((actual_name, predicted_name))
        task_labels[row["qid"]].update(row["keys"])
        per_slice[slice_name].append((actual_name, predicted_name))
        slice_labels[slice_name].update(row["keys"])
        if row["qid"] == "category":
            category_pairs.append((actual_name, predicted_name))
    ece = 0.0
    bin_ids = np.minimum((np.asarray(confidences) * 10).astype(int), 9)
    for bin_id in range(10):
        selected = np.flatnonzero(bin_ids == bin_id)
        if selected.size:
            ece += len(selected) / len(rows) * abs(np.mean([correct[i] for i in selected])
                                                   - np.mean([confidences[i] for i in selected]))
    category = classification_metrics(category_pairs, sorted({value for pair in category_pairs for value in pair})) \
        if category_pairs else None
    return {
        "decisions": len(rows), "accuracy": round(float(np.mean(correct)), 6),
        "category_accuracy": category["accuracy"] if category else None,
        "category_macro_f1": category["macro_f1"] if category else None,
        "ece": round(float(ece), 6), "brier": round(float(np.mean(briers)), 6),
        "nll": round(float(np.mean(losses)), 6),
        "tasks": {key: classification_metrics(value, sorted(task_labels[key]))
                  for key, value in sorted(per_task.items())},
        "slices": {key: classification_metrics(value, sorted(slice_labels[key]))
                   for key, value in sorted(per_slice.items())},
    }


def checkpoint_selection(metrics, baseline, max_recall_drop, min_recall=.05):
    def recalls(result):
        return {f"{slice_name}:{label}": values["recall"]
                for slice_name, section in result["slices"].items()
                for label, values in section["classes"].items() if values["support"]}

    current, initial = recalls(metrics), recalls(baseline)
    regressions = {key: round(initial[key] - current[key], 6) for key in initial}
    failures = {key: {"recall": current[key], "baseline_recall": initial[key]}
                for key, drop in regressions.items()
                if drop > max_recall_drop or current[key] < min_recall}
    worst_recall = min(current.values()) if current else 0.0
    task_macro_f1 = float(np.mean([task["macro_f1"] for task in metrics["tasks"].values()]))
    rank = [round(worst_recall, 6), round(task_macro_f1, 6), metrics["accuracy"]]
    return {"eligible": not failures, "rank": rank, "worst_class_recall": round(worst_recall, 6),
            "task_macro_f1": round(task_macro_f1, 6),
            "max_recall_drop": round(max(regressions.values(), default=0.0), 6),
            "guardrail_failures": failures}


def compact_predictions(rows, calibration):
    evidence = []
    for index, row in enumerate(rows):
        logits = np.asarray(row["logits"], dtype=float) / row_temperature(row, calibration, "pulse")
        probabilities = np.exp(logits - logits.max())
        probabilities /= probabilities.sum()
        predicted, actual = int(probabilities.argmax()), int(np.asarray(row["target"]).argmax())
        evidence.append({"sample_id": f"test-{index:06d}", "task": row["qid"],
                         "language": row["language"], "actual": row["keys"][actual],
                         "predicted": row["keys"][predicted],
                         "confidence": round(float(probabilities[predicted]), 6),
                         "correct": predicted == actual})
    return evidence
