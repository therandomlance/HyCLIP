from db import HyCLIP_DB
from config import HyCLIP_Config
from model import HyCLIP_Model
import hydrus_api

CFG = HyCLIP_Config()
MODEL = HyCLIP_Model(CFG.CLIP_MODEL)
DB = HyCLIP_DB(MODEL.dims, CFG.VECTOR_QUANT)
HY = hydrus_api.Client(access_key=CFG.API_KEY, api_url=CFG.API_URL)

tag = "species:lopunny"
num = 1000

QUERY = [f'system:limit={num}', tag]

response = HY.search_files(QUERY, file_sort_type=4, return_file_ids=True)
file_ids = response.get("file_ids", [])

# only files already ingested into HyCLIP have a stored embedding to centroid
ingested = [fid for fid in file_ids if DB.exists_hash_id(fid)]
embeddings = DB.get_embeddings(ingested)
centroid = DB.vec_centroid(embeddings)
if centroid is None:
	raise SystemExit(f'no ingested files matched "{tag}"')

DB.insert_tag(tag, centroid)

search_results = DB.search_embedding(centroid, MODEL.dims)