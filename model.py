import open_clip
import torch
import gc
from PIL import Image, ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True
import os
import concurrent.futures
from huggingface_hub import snapshot_download, hf_hub_download

class HyCLIP_Model():
	def __init__(self, model_name, verbose:bool=True):
		self.model_name = model_name
		self.PRETRAINED_TAG = "webli"
		self.VERBOSE = verbose
		
		if self.VERBOSE:
			print("loading config...")
		
		self.config = open_clip.get_model_config(self.model_name)
		self.dims = self.config["embed_dim"]

		self.get_device()

		self.model = None
		self.preprocess = None
		self.tokenizer = None

	def _assert_filepath(self, filepath:str):
		if not os.path.isfile(filepath):
			raise FileNotFoundError(f"model.py: filepath not found: {filepath}")

	def _assert_model_loaded(self):
		if not self.model:
			raise RuntimeError(f"model.py: model not loaded! - {self.model_name}")

	def get_device(self):
		if self.VERBOSE:
			print("getting device...")

		self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

		if self.VERBOSE:
			print(f"device: {self.device}")

	def check_filetype(self, filepath:str):
		IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
		return os.path.splitext(filepath)[1].lower() in IMAGE_EXTS
	
	# Returns the path to the model if it is already in the HF cache, else None
	def cached_path(self) -> str | None:
		repo = open_clip.pretrained.get_pretrained_cfg(self.model_name, self.PRETRAINED_TAG).get("hf_hub", "").rstrip("/")
		if not repo:
			return None
		try:
			return snapshot_download(repo, local_files_only=True)
		except Exception:
			return None

	def load_model(self):
		if self.VERBOSE:
			print(f"loading model: {self.model_name}")
		path = self.cached_path()
		if path:
			# Already cached locally - load without pinging huggingface
			self.model, self.preprocess = open_clip.create_model_from_pretrained(f"local-dir:{path}")
			self.tokenizer = open_clip.get_tokenizer(f"local-dir:{path}")
		else:
			repo = open_clip.pretrained.get_pretrained_cfg(self.model_name, self.PRETRAINED_TAG).get("hf_hub", "").rstrip("/")
			if repo:
				hf_hub_download(repo, "open_clip_config.json")
			self.model, self.preprocess = open_clip.create_model_from_pretrained(self.model_name, pretrained=self.PRETRAINED_TAG)
			self.tokenizer = open_clip.get_tokenizer(self.model_name)

		self.model.to(self.device)
		self.model.eval()

		if self.VERBOSE:
			print(f"model loaded.")

	def unload_model(self):
		self.model = None
		self.preprocess = None
		self.tokenizer = None
		# Dropping the refs only returns memory to torch's caching allocator;
		# collect + empty_cache hands the VRAM back to the GPU
		gc.collect()
		if self.device.type == "cuda":
			torch.cuda.empty_cache()

	def eval_image(self, image_path:str) -> list[float] | None:
		self._assert_filepath(image_path)
		self._assert_model_loaded()

		# I have no idea how this works, I just stole it from teh example scripts
		try:
			with Image.open(image_path) as im:
				image = self.preprocess(im).unsqueeze(0).to(self.device)
		except Exception as e:
			# Unreadable/unsupported/huge image (e.g. PIL DecompressionBombError) — caller treats None as a skip
			print(f"could not evaluate image {image_path}: {e}")
			return None
		with torch.inference_mode():
			embedding = self.model.encode_image(image)
			embedding = torch.nn.functional.normalize(embedding, dim=-1)

		return embedding.tolist()[0]

	def _preprocess_one(self, path:str) -> torch.Tensor | None:
		try:
			with Image.open(path) as I:
				return self.preprocess(I)
		except Exception as e:
			print(f"preprocess could not evaluate image {path}: {e}")
			return None

	def preprocess_image_batch(self, image_paths:list[str], num_workers:int=4) -> list["torch.Tensor | None"]:
		"""CPU/network half of eval_image_batch: read + preprocess, None per failed image.
		Split out so a caller can prefetch the next batch while the GPU runs this one."""
		self._assert_model_loaded()

		with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as ex:
			return list(ex.map(self._preprocess_one, image_paths))

	def encode_preprocessed(self, results:list["torch.Tensor | None"]) -> list[list[float] | None]:
		"""GPU half of eval_image_batch: stack the surviving tensors and embed them."""
		self._assert_model_loaded()

		tensors = [t for t in results if t is not None]
		valid = [i for i, t in enumerate(results) if t is not None]
		if not tensors:
			return [None] * len(results)

		batch = torch.stack(tensors).to(self.device)
		with torch.inference_mode():
			embeddings = self.model.encode_image(batch)
			embeddings = torch.nn.functional.normalize(embeddings, dim=-1)
		result = embeddings.tolist()

		out: list[list[float] | None] = [None] * len(results)
		for idx, emb in zip(valid, result):
			out[idx] = emb
		return out

	def eval_image_batch(self, image_paths:list[str], num_workers:int=4) -> list[list[float] | None]:
		return self.encode_preprocessed(self.preprocess_image_batch(image_paths, num_workers))

	def tokenize_text(self, text:str) -> list[float]:
		self._assert_model_loaded()
		
		tokenized_text = self.tokenizer(text).to(self.device)
		with torch.inference_mode():
			embedding = self.model.encode_text(tokenized_text)
			embedding = torch.nn.functional.normalize(embedding, dim=-1)

		return embedding.tolist()[0]
