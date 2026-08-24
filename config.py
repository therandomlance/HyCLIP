import os
import platform
from pathlib import Path
import json

class HyCLIP_Config:
	DEFAULT_CONFIG = {
		"API_URL": "http://localhost:45869",
		"API_KEY": None,
		"HYCLIP_API_URL": "http://localhost:8000",
		"TAG_SERVICE_KEY": None,
		"RATING_SERVICE_KEY": None,
		"VECTOR_QUANT": "UINT8",
		"CLIP_MODEL": "ViT-B-16-SigLIP2",
		"LOAD_MODEL_ON_STARTUP": False,
		"THUMB_SIZE": 200,
		"SEARCH_LIMIT": 100,
		"BUCKET_CACHE_TIMEOUT": 300,
		"EVAL_WORKERS": 8,
		"INGEST_BATCH_SIZE": 64
	}

	def __init__(self, cfg_path:str="config.json"):
		self.load_config(cfg_path)

	def load_config(self, path):
		self.config_path = path
		try:
			with open(self.config_path) as F:
				cfg = json.load(F)
		except FileNotFoundError:
			cfg = {}

		# File overrides defaults; anything missing falls back to the default
		self.cfg = {**self.DEFAULT_CONFIG, **cfg}

		for key, value in self.cfg.items():
			setattr(self, key, value)

	def save_config(self):
		for key in self.cfg:
			self.cfg[key] = getattr(self, key)

		with open(self.config_path, "w") as F:
			json.dump(self.cfg, F, indent=4)


if __name__ == "__main__":
	# Used by run.sh/ingest.sh: prints "<url> <host> <port>" for uvicorn to bind to.
	import urllib.parse as u

	c = HyCLIP_Config()
	url = os.environ.get("HYCLIP_API_URL") or c.HYCLIP_API_URL
	p = u.urlparse(url)
	print(url, p.hostname or "127.0.0.1", p.port or 80)
