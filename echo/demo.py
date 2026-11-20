from datetime import date, timedelta
from math import ceil, log2
from statistics import mean, median
from tempfile import TemporaryDirectory
from pathlib import Path
from time import perf_counter

from .archive import Archive
from .extract import Document

# Authored sample passages and independently specified relevant source IDs.
SAMPLES = [
    ("coral", "How coral reefs protect coastlines", "webpage",
     "Coral reefs form natural breakwaters in shallow tropical waters. Their rough structure dissipates wave energy before waves reach land, reducing erosion, storm surge and flooding. Living reefs also provide habitat for marine species.", 14),
    ("rain", "Rainwater harvesting for urban homes", "text",
     "Collect runoff from a rooftop using gutters and downpipes. A first-flush diverter discards the dirtiest water before a screened cistern stores the rest. Harvested rainfall can irrigate a garden and reduce demand on municipal supplies.", 12),
    ("habits", "Designing better habits", "note",
     "Habit stacking attaches a new behavior to an action already part of a daily routine. For instance, place a short stretching session immediately after making morning coffee. The established routine provides a reliable cue for the new action.", 11),
    ("batteries", "Lithium-ion battery care", "text",
     "High temperatures and prolonged exposure to very high charge accelerate battery degradation. Keeping a rechargeable lithium-ion cell between moderate charge levels can extend service life. This applies to many phones and laptops.", 9),
    ("water", "Water retention in urban landscapes", "webpage",
     "A rain garden is a sunken planted area that captures runoff from paved surfaces. Deep-rooted native plants and absorbent soil slow the flow and let water infiltrate, easing pressure on storm drains after heavy rainfall.", 8),
    ("sleep", "Sleep and memory consolidation", "note",
     "During sleep, neural activity replays patterns associated with recent experience. Memory consolidation stabilizes new knowledge and helps integrate it with existing knowledge. Sleep after learning can improve later retention.", 5),
    ("git", "Git branching guide", "text",
     "A Git branch is a movable reference to a commit. Branches support parallel work by letting contributors develop changes independently. A merge integrates changes from one line of development into another.", 4),
    ("trees", "How trees cool city streets", "webpage",
     "Tree canopies reduce surface temperatures by providing shade. Water released through leaf transpiration cools the surrounding air. Neighborhoods with tree cover can experience less intense urban heat during summer.", 3),
    ("review", "Spaced repetition notes", "note",
     "Spaced repetition improves long-term retention by reviewing material at increasing intervals. Instead of cramming the night before, distribute recall practice across several days. Reviewing just before forgetting helps reinforce memory.", 2),
    ("compost", "A field guide to home composting", "text",
     "Combine dry carbon-rich leaves with fresh nitrogen-rich scraps from the kitchen. Microbes decompose the mixture into compost. Turning the pile supplies oxygen, and keeping the mixture as damp as a wrung-out sponge supports decomposition.", 1),
    ("focus", "The science of focused work", "webpage",
     "Context switching disrupts concentration and leaves attention residue from the previous task. Long stretches of uninterrupted work reduce the cost of repeatedly reorienting. Group shallow tasks together and turn off unnecessary alerts.", 1),
    ("miso", "Miso ramen recipe notes", "note",
     "For a weeknight broth, warm vegetable stock with ginger and garlic. Whisk in miso after turning off the heat. Add cooked noodles, sliced scallions and mushrooms. Miso and dried mushrooms provide savory umami flavor.", 0),
]

QUERIES = [
    ("How can I remember what I study for longer?", {"review", "sleep"}, {}),
    ("The guide about working on different software changes at the same time", {"git"}, {}),
    ("What was the method for turning vegetable scraps into soil?", {"compost"}, {}),
    ("The article on reducing heat in neighborhoods with shade", {"trees"}, {"kinds": ("webpage",)}),
    ("Find my note about doing a small new action right after a familiar activity", {"habits"}, {"kinds": ("note",)}),
    ("Where did I save the tip for extending the life of a rechargeable phone?", {"batteries"}, {}),
    ("How can a city keep sudden downpours from overwhelming its drainage?", {"water"}, {"kinds": ("webpage",)}),
    ("How do structures beneath the sea help shield people on land from storms?", {"coral"}, {}),
    ("A recipe for a savory Japanese noodle soup", {"miso"}, {"kinds": ("note",)}),
    ("Which note described what the brain does overnight after learning?", {"sleep"}, {}),
    ("Where is the article about the mental cost of jumping between activities?", {"focus"}, {}),
    ("Can I collect water that falls onto my roof?", {"rain"}, {}),
]


def seed(archive: Archive) -> int:
    added = 0
    for key, title, kind, text, days_ago in SAMPLES:
        # Retain a downloadable plain-text original for the sample text files.
        original = text.encode("utf-8") if kind == "text" else None
        filename = f"{key}.txt" if original else ""
        document = Document(title, kind, [(None, text)], filename=filename, original=original)
        _, created = archive.add(document, date.today() - timedelta(days=days_ago), key)
        added += int(created)
    return added


def evaluate(archive: Archive) -> dict:
    k = 3
    with TemporaryDirectory(prefix="echo-evaluation-") as temp:
        benchmark = Archive(Path(temp), archive.get_model())
        start = perf_counter()
        count = seed(benchmark)
        indexing_ms = round((perf_counter() - start) * 1000, 1)

        # Each strategy sees the same corpus, labels, filters, and K.
        results = []
        for mode in ("keyword", "semantic", "contextual"):
            benchmark.search(QUERIES[0][0], mode, top_k=k)
            precision, recall, reciprocal, ndcg, latency = [], [], [], [], []
            details = []
            for query, relevant, filters in QUERIES:
                start = perf_counter()
                found = benchmark.search(query, mode, top_k=k, **filters)
                latency.append((perf_counter() - start) * 1000)
                ranks = [rank for rank, item in enumerate(found, 1) if item["sample_key"] in relevant]
                precision.append(len(ranks) / k)
                recall.append(len(ranks) / len(relevant))
                reciprocal.append(1 / ranks[0] if ranks else 0)
                ideal = sum(1 / log2(rank + 1) for rank in range(1, min(k, len(relevant)) + 1))
                ndcg.append(sum(1 / log2(rank + 1) for rank in ranks) / ideal)
                details.append({"query": query, "rank": ranks[0] if ranks else None})
            results.append({
                "mode": mode, "precision": mean(precision), "recall": mean(recall),
                "mrr": mean(reciprocal), "ndcg": mean(ndcg), "median_ms": round(median(latency), 2),
                "p95_ms": round(sorted(latency)[ceil(len(latency) * 0.95) - 1], 2),
                "queries": details,
            })
    return {"k": k, "sources": count, "query_count": len(QUERIES), "indexing_ms": indexing_ms, "results": results}


if __name__ == "__main__":
    from json import dumps
    from .model_setup import DATA_DIR

    print(dumps(evaluate(Archive(DATA_DIR)), indent=2))
