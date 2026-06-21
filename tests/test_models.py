def test_job_model():
    from models import Job

    job = Job(id="abc123", created=12345.0)
    assert job.id == "abc123"
    assert job.status == "queued"
    assert job.percent == 0.0
    assert job.speed == ""
    assert not hasattr(job, "event")


def test_job_model_dump_excludes_event():
    from models import Job

    job = Job(id="test")
    d = job.model_dump()
    assert "event" not in d
    assert d["id"] == "test"
    assert d["status"] == "queued"


def test_job_model_attribute_access():
    from models import Job

    job = Job(id="test")
    job.status = "downloading"
    job.percent = 50.0
    job.speed = "10MiB/s"
    assert job.status == "downloading"
    assert job.percent == 50.0
    assert job.speed == "10MiB/s"


def test_jobreq_model():
    from models import JobReq

    req = JobReq(url="https://youtu.be/abc")
    assert req.url == "https://youtu.be/abc"
    assert req.quality == "best"

    req2 = JobReq(url="https://youtu.be/abc", quality="1080p")
    assert req2.quality == "1080p"
