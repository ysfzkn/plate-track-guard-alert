import config, glob, os, tempfile
import app.routes as routes
from app.intrusion.person_detector import PersonDetector
from app.intrusion.zone_manager import AdHocZoneManager
from app.intrusion.classifier import IntrusionClassifier
from app.intrusion.models import Zone

vids=sorted(glob.glob(os.path.join(tempfile.gettempdir(),"gateguard_playground","*.mp4")),key=os.path.getmtime)
vid=vids[-1]
CAM=999999
pd=PersonDetector(model_path="yolov8n.pt",use_gpu=False,confidence=0.30); pd.preload()
routes._person_detector=pd

# tüm-kare bölge, HAREKET YAKALAMA AÇIK
z=Zone(id=1,camera_id=CAM,name="Cevre",polygon_points=[(0.001,0.001),(0.999,0.001),(0.999,0.999),(0.001,0.999)],
       is_night_only=False,min_loiter_sec=0,enabled=True,enable_motion_fallback=True)
zm=AdHocZoneManager({CAM:[z]})
clf=IntrusionClassifier(zm,min_confidence=0.30,warmup_sec=0,min_consecutive_frames=3,frame_gap_reset_sec=10.0)
clf.mark_camera_started(CAM)

out=os.path.join("static","test_outputs","_motiontest.mp4")
res=routes._run_e2e_video(vid, out, sample_rate=1, classifier=clf, camera_id=CAM, sim_hour=23)
print("motion_detector kuruldu mu? (bolgede fallback acik):", "EVET")
print("tespit (YOLO+hareket):", res["rule_stats"]["detections"])
print("bolgede:", res["rule_stats"]["in_zone"])
print("kural gecti:", res["rule_stats"]["passed_classifier"])
print("olay sayisi:", res["event_count"])
src=[e.get("source") for e in res["events"]]
print("olay kaynaklari:", src)
