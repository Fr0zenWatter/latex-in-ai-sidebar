"""Run: python -B scripts/test_project_chat.py (stdlib, no model request)."""
import json
from contextlib import closing
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch
from history import History
from chat import ChatJob, chat_context

with tempfile.TemporaryDirectory() as directory:
    path=Path(directory)/'paper.tex'
    path.write_text('First selection. Second selection.',encoding='utf-8')
    memory=History(path)
    answer={'reply':'Remembered terminology.','replacement':None}
    event={'type':'item.completed','item':{'type':'agent_message','text':json.dumps(answer)}}
    process=Mock(returncode=0)
    process.communicate.return_value=(json.dumps(event).encode() + b'\n{"type":"turn.completed"}',b'')
    data={'source':path.read_text(encoding='utf-8'),'selection':'First selection.',
          'messages':[{'role':'user','content':'Use energy norm throughout.'}]}
    context=chat_context(data,path)
    with patch('chat.codex_executable',return_value='codex.exe'),patch('chat.subprocess.Popen',return_value=process):
        job=ChatJob(context,memory,0);job.run()
        assert job.result['status']=='done' and job.result['memory_revision']==2
    state=History(path).chat_read()
    assert state['messages'][0]['selection']=='First selection.'
    assert state['messages'][0]['content']=='Use energy norm throughout.'
    assert json.loads(state['messages'][1]['content'])==answer
    terminal = {'type':'turn.completed'}
    quote = {'id':'quote-1', 'type':'mcp_tool_call', 'server':'tex_quotes', 'tool':'cite_tex',
             'status':'completed', 'result':{'content':[]}}
    invalid_outputs = [[event], [terminal, event], [event, terminal, terminal],
        [event, {'type':'turn.failed', 'error':{'message':'Failed'}}, terminal],
        [event, terminal, {'type':'error', 'message':'Failed after output'}],
        [[], event, terminal], [{'type':'item.completed', 'item':[]}, event, terminal],
        [{'type':'item.started', 'item':{**quote, 'status':'in_progress', 'result':None}}, event, terminal]]
    for change in ({'status':'failed'}, {'error':{'message':'Failed'}}, {'result':{'isError':True}},
                   {'result':None}, {'id':None}, {'status':'in_progress'}):
        invalid_outputs.append([{'type':'item.completed', 'item':{**quote, **change}}, event, terminal])
    for malformed in ([answer], {**answer,'status':'running'}, {**answer,'memory_revision':999},
                      {**answer,'segments':[]}, {'reply':'Missing replacement'}, {**answer,'reply':None}):
        invalid_outputs.append([{'type':'item.completed','item':{'type':'agent_message','text':json.dumps(malformed)}}, terminal])
    with patch('chat.codex_executable',return_value='codex.exe'),patch('chat.subprocess.Popen',return_value=process):
        for events in invalid_outputs:
            process.communicate.return_value=('\n'.join(json.dumps(item) for item in events).encode(),b'')
            failed=ChatJob(context,memory,2); failed.run()
            assert failed.result['status']=='error', (events,failed.result)
            assert memory.chat_read()==state, 'An incomplete or malformed answer must not write conversation history.'
    assert path.read_text(encoding='utf-8')==data['source']
    other=path.with_name('other.tex');other.touch()
    assert History(other).chat_read()==state, 'Files in one project share the conversation.'
    elsewhere=Path(directory)/'another';elsewhere.mkdir()
    assert History(elsewhere/'paper.tex').chat_read()['messages']==[], 'Projects stay isolated.'
    revision=memory.chat_append(2,'Explain the second passage.',answer,str(path),'Second selection.')
    assert revision==4 and len(memory.chat_read()['messages'])==4
    try:memory.chat_append(2,'Stale question',answer,str(path),'First selection.')
    except ValueError:pass
    else:raise AssertionError('Stale memory overwrote the conversation.')
    assert len(memory.chat_read()['messages'])==4
    new=memory.chat_new();assert new['messages']==[]
    with closing(memory.connect()) as db:assert db.execute('SELECT COUNT(*) FROM chat_messages').fetchone()[0]==5
    assert path.read_text(encoding='utf-8')==data['source']
print('PASS: project chat persistence, selection snapshots, reload, shared project files, isolation, stale writes and archived new chats')
print('PASS: failed/incomplete turns, failed/pending citation tools and forged response fields cannot write history')
