# Yutori Navi-Bench
A benchmark for evaluating web agents on everyday tasks directly on real websites.

Dataset: https://huggingface.co/datasets/yutori-ai/navi-bench

Blog post: https://yutori.com/blog/introducing-navigator

## Quick Start: Try a Task Yourself

Want to understand what the benchmark tasks look like? You can run them manually using our human-in-the-loop demo:

### Step 1: Install with Browser Support

```bash
# Using uv (recommended)
uv pip install -e ".[datasets]"
python -m playwright install chromium

# Or using pip
pip install -e ".[datasets]"
python -m playwright install chromium
```

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
