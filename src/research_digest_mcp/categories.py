"""What the arXiv category codes actually mean.

`cs.RO (104)` is not information. It is a code you either happen to know or
quietly stop reading, and a configuration screen made of codes you do not know
is a configuration screen you cannot use — which is how a fetch profile ends up
being whatever it was on day one forever.

Names and one-line descriptions are taken from arXiv's own category taxonomy.
Only the archives a research feed realistically pulls from are listed; anything
missing degrades to the raw code rather than to a guess, because inventing a
plausible-sounding description for a category is worse than admitting we do not
have one.
"""
from __future__ import annotations

from typing import Any, Dict, List

# code -> (short name, one line on what belongs in it)
CATEGORIES: Dict[str, tuple] = {
    # --- computer science -------------------------------------------------
    "cs.AI": ("Artificial Intelligence",
              "Agents, planning, reasoning, knowledge representation."),
    "cs.CL": ("Computation and Language",
              "Natural language processing. Where most LLM papers land."),
    "cs.LG": ("Machine Learning",
              "Learning methods and theory. The biggest firehose on arXiv."),
    "cs.MA": ("Multiagent Systems",
              "Several agents interacting: coordination, negotiation, markets."),
    "cs.SE": ("Software Engineering",
              "Building and testing software, including coding agents."),
    "cs.IR": ("Information Retrieval",
              "Search, ranking, recommendation, RAG."),
    "cs.CV": ("Computer Vision",
              "Images and video. Large, and mostly not about language."),
    "cs.CR": ("Cryptography and Security",
              "Attacks and defences, including prompt injection and jailbreaks."),
    "cs.HC": ("Human-Computer Interaction",
              "Interfaces, user studies, how people actually use these systems."),
    "cs.DC": ("Distributed and Cluster Computing",
              "Serving, scheduling, parallelism, inference infrastructure."),
    "cs.CY": ("Computers and Society",
              "Policy, ethics, labour, deployment consequences."),
    "cs.DB": ("Databases", "Storage, query processing, data management."),
    "cs.NE": ("Neural and Evolutionary Computing",
              "Architectures, neuroevolution, optimisation by search."),
    "cs.RO": ("Robotics", "Embodied control, manipulation, navigation."),
    "cs.GT": ("Computer Science and Game Theory",
              "Mechanism design, incentives, equilibria, auctions."),
    "cs.SI": ("Social and Information Networks",
              "Network structure, diffusion, online behaviour."),
    "cs.NI": ("Networking and Internet Architecture",
              "Protocols, routing, network measurement."),
    "cs.PL": ("Programming Languages",
              "Type systems, compilers, program analysis, synthesis."),
    "cs.LO": ("Logic in Computer Science",
              "Formal methods, verification, proof."),
    "cs.DS": ("Data Structures and Algorithms",
              "Algorithm design and complexity."),
    "cs.CE": ("Computational Engineering, Finance, and Science",
              "Applied numerical work, including computational finance."),
    "cs.SD": ("Sound", "Audio, speech, music."),
    "cs.MM": ("Multimedia", "Mixed-media systems and compression."),
    "cs.AR": ("Hardware Architecture", "Processors, accelerators, memory systems."),
    "cs.OS": ("Operating Systems", "Kernels, scheduling, virtualisation."),
    "cs.ET": ("Emerging Technologies", "Non-conventional computing substrates."),
    "cs.FL": ("Formal Languages and Automata Theory", "Grammars, automata, parsing."),

    # --- statistics -------------------------------------------------------
    "stat.ML": ("Machine Learning (Statistics)",
                "The statistical side of learning. Overlaps cs.LG heavily."),
    "stat.ME": ("Methodology",
                "How to do a study properly: design, inference, causal methods."),
    "stat.AP": ("Applications", "Statistics applied to a specific domain."),
    "stat.TH": ("Statistics Theory", "Estimation and inference theory."),
    "stat.CO": ("Computation", "Sampling, MCMC, computational statistics."),

    # --- economics and finance -------------------------------------------
    "econ.EM": ("Econometrics",
                "Identification, instruments, causal estimation from observational data."),
    "econ.GN": ("General Economics", "Applied and general economic work."),
    "econ.TH": ("Theoretical Economics", "Economic theory and mechanism design."),
    "q-fin.CP": ("Computational Finance", "Pricing, numerical methods, simulation."),
    "q-fin.TR": ("Trading and Market Microstructure",
                 "Order books, execution, market making."),
    "q-fin.PM": ("Portfolio Management", "Allocation, optimisation, risk budgeting."),
    "q-fin.RM": ("Risk Management", "Measuring and hedging financial risk."),
    "q-fin.ST": ("Statistical Finance", "Empirical properties of financial series."),
    "q-fin.GN": ("General Finance", "Everything else in finance."),
    "q-fin.MF": ("Mathematical Finance", "Stochastic models of markets."),
    "q-fin.EC": ("Economics (q-fin)", "Economics as it relates to finance."),
    "q-fin.PR": ("Pricing of Securities", "Derivative and asset pricing."),

    # --- other sciences ---------------------------------------------------
    "q-bio.NC": ("Neurons and Cognition",
                 "Neuroscience and cognitive modelling. Read for memory and learning mechanisms."),
    "q-bio.QM": ("Quantitative Methods", "Methods for biological data."),
    "physics.soc-ph": ("Physics and Society",
                       "Networks, collective behaviour, models of social systems."),
    "physics.data-an": ("Data Analysis, Statistics and Probability",
                        "Analysis methods from the physics side."),
    "quant-ph": ("Quantum Physics", "Quantum computing, information, algorithms."),
    "nlin.AO": ("Adaptation and Self-Organizing Systems",
                "Emergence, adaptation, complex systems."),
    "nlin.CD": ("Chaotic Dynamics", "Nonlinear dynamics and chaos."),
    "math.OC": ("Optimization and Control",
                "Optimisation theory, control, operations research."),
    "math.ST": ("Statistics Theory", "The mathematical statistics archive."),
    "math.PR": ("Probability", "Probability theory."),
    "math.NA": ("Numerical Analysis", "Numerical methods and their error behaviour."),
    "eess.SY": ("Systems and Control", "Control theory, feedback, system identification."),
    "eess.AS": ("Audio and Speech Processing", "Speech recognition and synthesis."),
    "eess.IV": ("Image and Video Processing", "Signal-processing side of imaging."),
    "eess.SP": ("Signal Processing", "Signals, estimation, sensing."),
}

# Which archive a code belongs to, for grouping a picker.
ARCHIVE_NAMES = {
    "cs": "Computer science",
    "stat": "Statistics",
    "econ": "Economics",
    "q-fin": "Quantitative finance",
    "q-bio": "Quantitative biology",
    "physics": "Physics",
    "math": "Mathematics",
    "eess": "Electrical engineering",
    "nlin": "Nonlinear sciences",
    "quant-ph": "Quantum physics",
}

# A starting profile for someone who has not got one. Not a default that is
# silently applied -- an offer the Profile screen can make, so the first
# question is "is this you?" rather than "what is a category?".
STARTER_PROFILES = {
    "ai-systems": {
        "label": "AI systems and agents",
        "blurb": "Agents, evaluation, retrieval, and the engineering around them.",
        "core": {"categories": ["cs.AI", "cs.CL", "cs.LG", "cs.MA", "cs.SE"],
                 "topics": ["agent", "evaluation", "benchmark", "retrieval",
                            "tool use", "reasoning", "reliability", "hallucination"]},
        "complementary": {"categories": ["cs.IR", "cs.HC"],
                          "topics": ["ranking", "interface", "trust", "explainability"]},
    },
    "ml-research": {
        "label": "Machine learning research",
        "blurb": "Methods, training, architectures and the theory underneath.",
        "core": {"categories": ["cs.LG", "stat.ML", "cs.AI"],
                 "topics": ["optimization", "generalization", "scaling",
                            "representation", "fine-tuning", "distillation"]},
        "complementary": {"categories": ["math.OC", "cs.NE"],
                          "topics": ["convergence", "regularization"]},
    },
    "quant-finance": {
        "label": "Quantitative finance",
        "blurb": "Markets, pricing, portfolios, and ML applied to them.",
        "core": {"categories": ["q-fin.CP", "q-fin.TR", "q-fin.PM", "q-fin.ST"],
                 "topics": ["volatility", "portfolio", "backtest", "market microstructure",
                            "forecasting", "risk"]},
        "complementary": {"categories": ["cs.LG", "econ.EM"],
                          "topics": ["time series", "causal inference"]},
    },
    "security": {
        "label": "Security and safety",
        "blurb": "Attacks, defences, and the safety of deployed models.",
        "core": {"categories": ["cs.CR", "cs.AI", "cs.CL"],
                 "topics": ["prompt injection", "jailbreak", "adversarial", "guardrails",
                            "red teaming", "alignment", "privacy"]},
        "complementary": {"categories": ["cs.SE", "cs.CY"],
                          "topics": ["vulnerability", "policy"]},
    },
}

# Method words. The stretch tier matches on these rather than on subject
# matter, which is the whole point of it: you are not reading econometrics for
# the economics, you are reading it for how they establish a claim.
STRUCTURAL_SUGGESTIONS = [
    "causal inference", "identification", "confounding", "instrumental variable",
    "ablation", "replication", "reproducible", "pre-registration",
    "measurement error", "construct validity", "selection bias", "power analysis",
    "effect size", "significance", "confidence interval", "sensitivity analysis",
    "falsification", "robustness", "distribution shift", "external validity",
    "randomized controlled", "difference-in-differences", "regression discontinuity",
    "benchmark contamination", "data leakage", "inter-rater reliability",
]


def describe(code: str) -> Dict[str, str]:
    """Name and blurb for a category code. Unknown codes say so."""
    name, blurb = CATEGORIES.get(code, ("", ""))
    return {"code": code, "name": name, "blurb": blurb,
            "archive": ARCHIVE_NAMES.get(code.split(".")[0], "")}


def catalogue() -> List[Dict[str, Any]]:
    """Every known category, grouped by archive, for a picker."""
    groups: Dict[str, List[Dict[str, str]]] = {}
    for code in CATEGORIES:
        archive = code.split(".")[0]
        groups.setdefault(archive, []).append(describe(code))
    return [
        {"archive": archive,
         "label": ARCHIVE_NAMES.get(archive, archive),
         "categories": sorted(rows, key=lambda r: r["code"])}
        for archive, rows in sorted(groups.items(),
                                    key=lambda kv: ARCHIVE_NAMES.get(kv[0], kv[0]))
    ]
