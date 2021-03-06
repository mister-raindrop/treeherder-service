# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.

import pytest
import simplejson as json
import urllib2
from django.conf import settings
import gzip
import zlib

from django.utils.six import BytesIO

from ..sampledata import SampleData

from treeherder.model.derived import ArtifactsModel


@pytest.fixture
def jobs_with_local_log(initial_data):
    log = ("mozilla-inbound_ubuntu64_vm-debug_test-"
           "mochitest-other-bm53-tests1-linux-build122")
    sample_data = SampleData()
    url = "file://{0}".format(
        sample_data.get_log_path("{0}.txt.gz".format(log)))

    job = sample_data.job_data[0]

    # substitute the log url with a local url
    job['job']['log_references'][0]['url'] = url
    return [job]


@pytest.fixture
def jobs_with_local_talos_log(initial_data):
    sample_data = SampleData()
    url = sample_data.get_performance_logs()[0]

    job = sample_data.job_data[0]

    # substitute the log url with a local url
    job['job']['log_references'][0]['url'] = url
    return [job]


@pytest.fixture
def jobs_with_local_mozlog_log(initial_data):
    log = ("plain-chunked_raw.log")
    sample_data = SampleData()
    url = "file://{0}".format(
        sample_data.get_log_path("{0}.gz".format(log)))

    # sample url to test with a real log, during development
    # url = "http://mozilla-releng-blobs.s3.amazonaws.com/blobs/try/sha512/6a690d565effa5a485a9385cc62eccd59feaa93fa6bb167073f012a105dc33aeaa02233daf081426b5363cd9affd007e42aea2265f47ddbc334a4493de1879b5"
    job = sample_data.job_data[0]

    # substitute the log url with a local url
    job['job']['log_references'][0]['url'] = url
    job['job']['log_references'][0]['name'] = 'mozlog_json'
    return [job]


@pytest.fixture
def mock_mozlog_get_log_handler(monkeypatch):

    def _get_log_handle(mockself, url):
        response = urllib2.urlopen(
               url,
               timeout=settings.TREEHERDER_REQUESTS_TIMEOUT
        )
        return gzip.GzipFile(fileobj=BytesIO(response.read()))

    import treeherder.etl.common
    monkeypatch.setattr(treeherder.log_parser.artifactbuilders.MozlogArtifactBuilder,
                        'get_log_handle', _get_log_handle)


def test_parse_log(jm, initial_data, jobs_with_local_log, sample_resultset,
                   mock_send_request, mock_get_remote_content):
    """
    check that at least 3 job_artifacts get inserted when running
    a parse_log task for a successful job
    """

    jm.store_result_set_data(sample_resultset)

    jobs = jobs_with_local_log
    for job in jobs:
        # make this a successful job, to check it's still parsed for errors
        job['job']['result'] = "success"
        job['revision_hash'] = sample_resultset[0]['revision_hash']

    jm.store_job_data(jobs)
    jm.process_objects(1, raise_errors=True)

    job_id = jm.get_jobs_dhub().execute(
        proc="jobs_test.selects.row_by_guid",
        placeholders=[jobs[0]['job']['job_guid']]
    )[0]['id']

    job_artifacts = jm.get_jobs_dhub().execute(
        proc="jobs_test.selects.job_artifact",
        placeholders=[job_id]
    )

    jm.disconnect()

    # we must have at least 3 artifacts:
    # 1 for the log viewer
    # 1 for the job artifact panel
    # 1 for the bug suggestions
    assert len(job_artifacts) >= 3


# json-log parsing is disabled due to bug 1152681.
@pytest.mark.xfail
def test_parse_mozlog_log(jm, initial_data, jobs_with_local_mozlog_log,
                          sample_resultset, mock_send_request,
                          mock_get_remote_content,
                          mock_mozlog_get_log_handler
                          ):
    """
    check parsing the structured log creates a ``structured-faults`` artifact
    """

    jm.store_result_set_data(sample_resultset)

    jobs = jobs_with_local_mozlog_log
    for job in jobs:
        job['job']['result'] = "testfailed"
        job['revision_hash'] = sample_resultset[0]['revision_hash']

    jm.store_job_data(jobs)
    jm.process_objects(1, raise_errors=True)

    job_id = jm.get_jobs_dhub().execute(
        proc="jobs_test.selects.row_by_guid",
        placeholders=[jobs[0]['job']['job_guid']]
    )[0]['id']

    job_artifacts = jm.get_jobs_dhub().execute(
        proc="jobs_test.selects.job_artifact",
        placeholders=[job_id]
    )

    jm.disconnect()

    artifact = [x for x in job_artifacts if x['name'] == 'json_log_summary']
    assert len(artifact) >= 1

    all_errors = json.loads(zlib.decompress(artifact[0]['blob']))['all_errors']
    warnings = [x for x in all_errors if
                x['action'] == 'log' and x['level'] == "WARNING"]
    fails = [x for x in all_errors if
             x['action'] == 'test_status' and x['status'] == "FAIL"]

    assert len(warnings) == 106
    assert len(fails) == 3


def test_parse_talos_log(jm, test_project, initial_data, jobs_with_local_talos_log,
                         sample_resultset, mock_send_request, mock_get_remote_content):
    """
    check that performance job_artifacts get inserted when running
    a parse_log task for a talos job
    """

    jm.store_result_set_data(sample_resultset)

    jobs = jobs_with_local_talos_log
    jm.store_job_data(jobs)
    jm.process_objects(1, raise_errors=True)

    with ArtifactsModel(test_project) as artifacts_model:
        artifact_list = artifacts_model.get_performance_artifact_list(0, 10)
    assert len(artifact_list) >= 1  # should parse out at least one perf artifact


def test_bug_suggestions_artifact(jm, initial_data, jobs_with_local_log,
                                  sample_resultset, mock_send_request,
                                  mock_get_remote_content
                                  ):
    """
    check that at least 3 job_artifacts get inserted when running
    a parse_log task for a failed job, and that the number of
    bug search terms/suggestions matches the number of error lines.
    """
    jm.store_result_set_data(sample_resultset)

    jobs = jobs_with_local_log
    for job in jobs:
        job['job']['result'] = "testfailed"
        job['revision_hash'] = sample_resultset[0]['revision_hash']

    jm.store_job_data(jobs)
    jm.process_objects(1, raise_errors=True)

    job_id = jm.get_jobs_dhub().execute(
        proc="jobs_test.selects.row_by_guid",
        placeholders=[jobs[0]['job']['job_guid']]
    )[0]['id']

    job_artifacts = jm.get_jobs_dhub().execute(
        proc="jobs_test.selects.job_artifact",
        placeholders=[job_id]
    )

    jm.disconnect()

    # we must have at least 3 artifacts:
    # 1 for the log viewer
    # 1 for the job artifact panel
    # 1 for the bug suggestions
    assert len(job_artifacts) >= 3

    structured_log_artifact = [artifact for artifact in job_artifacts
                               if artifact["name"] == "text_log_summary"][0]
    bug_suggestions_artifact = [artifact for artifact in job_artifacts
                                if artifact["name"] == "Bug suggestions"][0]
    structured_log = json.loads(zlib.decompress(structured_log_artifact["blob"]))

    all_errors = structured_log["step_data"]["all_errors"]
    bug_suggestions = json.loads(zlib.decompress(bug_suggestions_artifact["blob"]))

    # we must have one bugs item per error in bug_suggestions.
    # errors with no bug suggestions will just have an empty
    # bugs list
    assert len(all_errors) == len(bug_suggestions)

    # We really need to add some tests that check the values of each entry
    # in bug_suggestions, but for now this is better than nothing.
    expected_keys = set(["search", "search_terms", "bugs"])
    for failure_line in bug_suggestions:
        assert set(failure_line.keys()) == expected_keys
