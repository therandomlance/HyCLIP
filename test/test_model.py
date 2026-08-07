import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import HyCLIP_Config
from model import HyCLIP_Model

TEST_DIR = Path(__file__).resolve().parent
IMG_DIR = TEST_DIR / "test_images"

N_EVAL = 8  # number of images to evaluate per run


def get_images(n: int) -> list[Path]:
	imgs = sorted(f for f in IMG_DIR.iterdir() if f.suffix.lower() in (".jpg", ".png", ".jpeg"))
	assert len(imgs) >= n, f"need at least {n} images, found {len(imgs)}"
	return imgs[:n]


def main():
	cfg = HyCLIP_Config()
	model_name = cfg.CLIP_MODEL

	model = HyCLIP_Model(model_name, verbose=False)
	assert model.dims == model.config["embed_dim"], "dims should match config"

	# ---- load / unload ----
	model.load_model()
	assert model.model is not None and model.preprocess is not None
	assert model.tokenizer is not None

	# ---- eval_image ----
	images = get_images(N_EVAL)
	for path in images:
		emb = model.eval_image(str(path))
		assert emb is not None, f"eval_image returned None for {path.name}"
		assert len(emb) == model.dims, f"embedding dim {len(emb)} != {model.dims}"
		assert all(isinstance(v, float) for v in emb), "embedding should be floats"

	# ---- embeddings should differ between distinct images ----
	emb0 = model.eval_image(str(images[0]))
	emb1 = model.eval_image(str(images[1]))
	assert emb0 != emb1, "two distinct images produced identical embeddings"

	# ---- tokenize_text ----
	text_emb = model.tokenize_text("a photo of a cat")
	assert len(text_emb) == model.dims, "text embedding dim mismatch"
	assert text_emb != emb0, "text embedding should differ from an image embedding"

	# ---- unload ----
	model.unload_model()
	assert model.model is None and model.preprocess is None and model.tokenizer is None

	print(f"ALL TESTS PASSED (model={model_name}, {len(images)} images evaluated)")


if __name__ == "__main__":
	main()
