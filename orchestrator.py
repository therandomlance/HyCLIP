from config import HyCLIP_Config
from db import HyCLIP_DB
from model import HyCLIP_Model
import hydrus_api

class Orchestrator():
	def __init__(self):
		self.CFG = HyCLIP_Config()
		self.MODEL = HyCLIP_Model(self.CFG.CLIP_MODEL)
		self.DB = HyCLIP_DB(self.MODEL.dims, self.CFG.VECTOR_QUANT)

		if not self.CFG.API_KEY:
			raise HTTPException(status_code=503, detail="hydrus API_KEY not configured")
		if not self.CFG.API_URL:
			raise HTTPException(status_code=503, detail="hydrus API_URL not configured")

		self.HY = hydrus_api.Client(api_url=self.CFG.API_URL, access_key=self.CFG.API_KEY)