"""Run branch deployment against disposable Git repos and mocked system services."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]


def run(args,cwd,**kwargs):
    return subprocess.run(args,cwd=cwd,text=True,capture_output=True,check=True,**kwargs)


def git(repo,*args):
    return run(['git',*args],repo).stdout.strip()


def executable(path,source):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('#!/usr/bin/env bash\nset -e\n'+source+'\n')
    path.chmod(0o755)


@pytest.fixture
def deployment(tmp_path):
    origin=tmp_path/'origin.git';author=tmp_path/'author';app=tmp_path/'app'
    run(['git','init','--bare','--initial-branch=main',str(origin)],tmp_path)
    run(['git','clone',str(origin),str(author)],tmp_path)
    git(author,'config','user.email','test@example.invalid');git(author,'config','user.name','Test')
    (author/'.gitignore').write_text('.venv/\nweb/dist/\n')
    (author/'frontend').mkdir();(author/'frontend/package.json').write_text('{}')
    (author/'deploy').mkdir()
    executable(author/'deploy/backup.sh','printf "backup:%s\\n" "$(git rev-parse HEAD)" >> "$DEPLOY_TEST_LOG"')
    (author/'version.txt').write_text('stable')
    git(author,'add','.');git(author,'commit','-m','stable');stable=git(author,'rev-parse','HEAD')
    git(author,'push','origin','main')
    run(['git','clone',str(origin),str(app)],tmp_path)
    git(author,'switch','-c','feature/test')
    (author/'version.txt').write_text('optimized');git(author,'commit','-am','optimized')
    optimized=git(author,'rev-parse','HEAD');git(author,'push','origin','feature/test')
    script=(ROOT/'deploy/update-git.sh').read_text()
    for before,after in {
        '/www/wwwroot/jiwu':str(app),'/www/server/jiwu-data':str(tmp_path/'data'),
        '/www/server/jiwu-backups':str(tmp_path/'backups'),'/etc/jiwu.env':str(tmp_path/'service.env'),
        '/run/lock/jiwu-update.lock':str(tmp_path/'update.lock'),
        '/etc/systemd/system':str(tmp_path/'systemd'),
        '[[ $EUID == 0 ]]':'[[ 1 == 1 ]]',
    }.items():script=script.replace(before,after)
    update=tmp_path/'update.sh';update.write_text(script)
    (tmp_path/'service.env').write_text('fixture')
    tools=tmp_path/'tools';tools.mkdir()
    log=tmp_path/'commands.log'
    executable(tools/'systemctl','''printf 'service:%s\\n' "$*" >> "$DEPLOY_TEST_LOG"
case "$1" in
 is-active) [[ ${*: -1} == jiwu ]];;
 cat) exit 1;;
 *) exit 0;;
esac''')
    executable(tools/'install','mkdir -p -- "${@: -1}"')
    for name in ('node','npm','flock','chown','chgrp','chmod','pg_dump','runuser'):
        executable(tools/name,'exit 0')
    executable(tools/'curl','printf \'{"status":"ok"}\\n\'')
    executable(app/'.venv/bin/python','''if [[ $1 == - ]]; then cat >/dev/null; printf 'https://example.invalid\\n'; fi''')
    env=os.environ | {'PATH':str(tools)+os.pathsep+os.environ['PATH'],'DEPLOY_TEST_LOG':str(log)}
    def deploy(*args):
        return subprocess.run(['bash',str(update),*args],cwd=app,env=env,text=True,capture_output=True)
    return app,author,stable,optimized,log,tmp_path/'backups',deploy


def test_deploy_feature_and_explicit_rollback_preserve_main_and_back_up_first(deployment):
    app,_,stable,optimized,log,backups,deploy=deployment
    result=deploy('--branch','feature/test')
    assert result.returncode==0,result.stdout+result.stderr
    assert git(app,'branch','--show-current')=='feature/test'
    assert git(app,'rev-parse','HEAD')==optimized
    assert git(app,'rev-parse','main')==stable
    assert 'backup:'+stable in log.read_text()
    snapshots=list(backups.iterdir())
    assert len(snapshots)==1
    assert (snapshots[0]/'previous-branch.txt').read_text().strip()=='main'
    assert (snapshots[0]/'previous-commit.txt').read_text().strip()==stable
    assert (snapshots[0]/'previous-code.tar.gz').is_file()
    rejected=deploy('--branch','main')
    assert rejected.returncode!=0
    assert git(app,'rev-parse','HEAD')==optimized and len(list(backups.iterdir()))==1
    result=deploy('--branch','main','--rollback')
    assert result.returncode==0,result.stdout+result.stderr
    assert git(app,'branch','--show-current')=='main'
    assert git(app,'rev-parse','HEAD')==stable and len(list(backups.iterdir()))==2
    assert 'backup:'+optimized in log.read_text()


def test_dirty_checkout_stops_before_service_or_code_changes(deployment):
    app,_,stable,_,log,backups,deploy=deployment
    (app/'version.txt').write_text('local edits')
    result=deploy('--branch','feature/test')
    assert result.returncode!=0 and '本地代码修改' in result.stderr
    assert git(app,'rev-parse','HEAD')==stable
    assert not log.exists() and not backups.exists()


def test_default_update_requires_main_and_rejects_divergent_feature(deployment):
    app,author,stable,_,log,_,deploy=deployment
    git(author,'switch','main')
    (author/'version.txt').write_text('stable update');git(author,'commit','-am','stable update');git(author,'push','origin','main')
    result=deploy()
    assert result.returncode==0,result.stdout+result.stderr
    assert git(app,'branch','--show-current')=='main'
    assert (app/'version.txt').read_text()=='stable update'
    before=log.read_text()
    result=deploy('--branch','feature/test')
    assert result.returncode!=0 and '分叉' in result.stderr
    assert log.read_text()==before
