# Yutori Navi-Bench
A benchmark for evaluating web agents on everyday tasks directly on real websites.

Dataset: https://huggingface.co/datasets/yutori-ai/navi-bench

Blog post: https://yutori.com/blog/introducing-navigator

## Quick Start: Try a Task Yourself

Want to understand what the benchmark tasks look like? You can run them manually using our human-in-the-loop demo:

### Step 1: Install with Browser Support

We recommend installing with [uv](https://docs.astral.sh/uv/getting-started/installation/):
```bash
uv sync --extra eval
source .venv/bin/activate
python -m playwright install chromium webkit
```

<details>
<summary>Or, using raw pip:</summary>
```bash
pip install -e ".[eval]"
python -m playwright install chromium webkit
```
</details>

### Step 2: Run the Demo

```bash
python -m demo
```

## Usage

```python
from datasets import load_dataset
from navi_bench.base import DatasetItem, instantiate

# Load dataset from HF
dataset = load_dataset("yutori-ai/navi-bench")

# Load a task from the dataset
task_item = DatasetItem.model_validate(dataset[0])

# Generate the task configuration
task_config = task_item.generate_task_config()

# Access task details
print(f"Task: {task_config.task}")
print(f"URL: {task_config.url}")
print(f"Evaluation Config: {task_config.eval_config}")

# Instantiate evaluator when starting the agent task
agent = ...
evaluator = instantiate(task_config.eval_config)

for _ in range(max_steps):
    # Agent takes a step
    ...

    # Update evaluator
    await evaluator.update(...)

# Get the final evaluation result
eval_result = await evaluator.compute()
```

*Note: most evaluators rely on site state for verification, so ensure the verifier is run before closing the browser window*

## Evaluation

We provide an evaluation script for the [Yutori n1](https://yutori.com/blog/introducing-navigator) model. You can use it as a reference for evaluating your own agents.

### Setup

1. Authenticate with Yutori:

   ```bash
   yutori auth login
   ```
   This will open Yutori in your browser and save your API key locally to `~/.yutori/config.json`.

   <details>
   <summary>Or, set the API key manually:</summary>

   ```bash
   export YUTORI_API_KEY=yt-...
   ```
   If both are present, the environment variable takes precedence over saved credentials.
   </details>

2. (Optional, but recommended) Use a remote browser provider (such as [BrightData](https://brightdata.com/products/scraping-browser)) to avoid getting blocked by certain websites.
    - By default, the eval script uses a remote browser connected via the `BROWSER_CDP_URL` environment variable for sites that tend to block automated browsers (apartments.com, resy.com).
    - If `BROWSER_CDP_URL` is not set, it falls back to a local browser, which may get blocked and lead to crashes. In that case, you can re-run the eval script after the first run with `--eval_concurrency 2` to retry the crashed tasks.

### Run

Evaluate on a single sample:

```bash
python -m evaluation.eval_n1 \
    --dataset_include_domains 'craigslist' \
    --dataset_max_samples 1
```

Evaluate on the full dataset (recommended to specify `BROWSER_CDP_URL` to avoid being blocked by certain websites):

```bash
BROWSER_CDP_URL=... \
  python -m evaluation.eval_n1
```

Optionally, evaluate on other datasets that share the same schema (e.g., [Halluminate Westworld](https://github.com/Halluminate/westworld)):

```bash
HALLUMINATE_API_KEY=... \
  python -m evaluation.eval_n1 \
    --dataset_name 'Halluminate/westworld'
```

### Results

The results on the full Navi-Bench dataset may look like:

![Sample results](assets/sample-results-navi-bench.png)

Where we print the number of finished/crashed tasks and three scores:
- **Lower Bound**: treat crashed tasks as score=0, then average across all the tasks
- **Excl. Crashed**: exclude crashed tasks, then average across the rest of the tasks
- **Upper Bound**: treat crashed tasks as score=1, then average across all the tasks

Results are saved to `results_n1/` by default. The script automatically resumes from existing results, so you can re-run to retry any crashed tasks. To start fresh, delete the directory or pass a different `--eval_save_dir`.

Each task gets its own sub-directory containing a `visualization.html` file that lets you step through the agent's trajectory with annotated screenshots.

## Dataset

Navi-Bench dataset is available on [HuggingFace](https://huggingface.co/datasets/yutori-ai/navi-bench). It consists of 100 tasks from five real websites: Apartments, Craigslist, OpenTable, Resy, and Google Flights.

Optionally, you may check out [Westworld](https://github.com/Halluminate/westworld), a benchmark from Halluminate featuring five simulated environments for e-commerce and travel tasks. Both datasets share the same format thus can be directly concatenated for joint evaluation.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Citation

If you use Yutori Navi-Bench in your research, please cite:

```bibtex
@misc{yutori2025navigator,
  author       = {Yutori},
  title        = {Introducing Navigator},
  howpublished = {\url{https://yutori.com/blog/introducing-navigator}},
  note         = {Yutori Blog},
  year         = {2025},
}
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Contact

For questions or issues, please open an issue on [GitHub](https://github.com/yutori-ai/navi-bench/issues).
