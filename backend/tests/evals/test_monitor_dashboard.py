import json
import pytest
from assistant.orchestrator.monitor_dashboard import report_summary, build_snapshot, render, main


def row(**kwargs):
    return dict(status='ok',label='pass',expected='pass',duration_ms=12,**kwargs)


def test_separate_raw_final_and_missing_grades():
    rows=[row(),dict(status='ok',label='fail',expected='fail',model_label='pass',calendar_guard={},label_overridden=True,duration_ms=30),dict(status='provider_error',label='abstain',expected='fail'),dict(status='ok',label='abstain',expected='abstain')]
    result=report_summary(dict(rows=rows,summary={'agreement_all_cases':1},metadata={'mode':'live'}),'run')
    assert result['model_agreement']['value']==.5
    assert result['final_agreement']['value']==.75
    assert result['provider_errors']==1 and result['semantic_abstentions']==1
    assert result['failure_detection']['value']==.5
    assert result['guard_overrides']==1 and result['guard_matches']==1
    assert result['latency_samples']==2


def test_unreviewed_not_in_agreement_denominator():
    r=row()
    r['expected']=None
    result=report_summary({'rows':[row(),r]},'run')
    assert result['label_coverage']['value']==.5
    assert result['final_agreement']=={'value':1.,'numerator':1,'denominator':1}
    assert result['uncertainty_recall']['value'] is None


@pytest.mark.parametrize('duration',[None,-1,float('nan'),float('inf'),True,'40'])
def test_invalid_timing_is_missing(duration):
    r=row()
    r['duration_ms']=duration
    result=report_summary({'rows':[r]},'run')
    assert result['p95_ms'] is None and result['latency_samples']==0


@pytest.mark.parametrize('mode',['replay_contract_only','deterministic_postprocess_of_preserved_live_outputs','unknown'])
def test_nonlive_timing_never_presented_as_live(mode):
    result=report_summary({'rows':[row()],'metadata':{'mode':mode}},'run')
    assert result['live_latency'] is False


def test_no_raw_content_or_html_injection(tmp_path):
    r=row()
    r.update(id='PRIVATE-ID',scores={'reason':'PRIVATE-REASON'},response='PRIVATE-RESPONSE',evidence='PRIVATE-EVIDENCE')
    report=tmp_path/'input.json'
    report.write_text(json.dumps({'rows':[r],'metadata':{'model':'<script>alert(1)</script>'}}))
    snapshot=build_snapshot([report])
    html=render(snapshot)
    for private in ['PRIVATE-ID','PRIVATE-REASON','PRIVATE-RESPONSE','PRIVATE-EVIDENCE']:
        assert private not in html and private not in json.dumps(snapshot)
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html


def test_duplicates_and_summary_not_trusted(tmp_path):
    a=tmp_path/'a.json'
    b=tmp_path/'b.json'
    a.write_text(json.dumps({'rows':[row()],'summary':{'agreement_all_cases':0}}))
    b.write_text(json.dumps({'rows':[row()],'summary':{'agreement_all_cases':.5}}))
    result=build_snapshot([a,b])
    assert len(result['reports'])==1 and result['duplicate_reports_skipped']==1
    assert result['reports'][0]['final_agreement']['value']==1


def test_empty_snapshot_not_healthy():
    snapshot=build_snapshot([])
    html=render(snapshot)
    assert 'No judge reports' in html and 'production health are unknown' in html
    assert report_summary({'rows':[]},'empty')['valid_outputs']['value'] is None


def test_runtime_logs_and_quality_kept_separate(tmp_path):
    log=tmp_path/'logs.jsonl'
    record=dict(schema_version=1,service='kairo',event='span',trace_id='x',span_id='y',stage='turn',duration_ms=20)
    log.write_text(json.dumps(record)+'\n'+json.dumps(record)+'\n'+'bad-json\n')
    snapshot=build_snapshot([],log)
    assert not snapshot['reports']
    assert snapshot['runtime']['observed_turn_spans']==1
    assert snapshot['runtime']['duplicate_records']==1
    assert snapshot['runtime']['malformed_records']==1


@pytest.mark.parametrize('bad',[{'status':'bogus','label':'pass'}, {'status':'ok','label':'bad'}, {'status':'ok','label':'fail','label_overridden':True}])
def test_rejects_invalid_or_unverifiable_grades(bad):
    with pytest.raises(ValueError):
        report_summary({'rows':[bad]},'bad')


def test_output_cannot_overwrite_input(tmp_path,monkeypatch):
    p=tmp_path/'input.json'
    p.write_text('{"rows":[]}')
    monkeypatch.setattr('sys.argv',['monitor_dashboard',str(p),'--output',str(p)])
    with pytest.raises(SystemExit):
        main()
    assert p.read_text()=='{"rows":[]}'


def test_cli_writes_local_html_and_summary(tmp_path,monkeypatch):
    html=tmp_path/'dashboard.html'
    summary=tmp_path/'dashboard.json'
    monkeypatch.setattr('sys.argv',['monitor_dashboard','--output',str(html),'--summary',str(summary)])
    assert main()==0
    assert 'Kairo evaluation monitor' in html.read_text()
    assert json.loads(summary.read_text())['reports']==[]
