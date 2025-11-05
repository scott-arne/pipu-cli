import sys
# noinspection PyPackageRequirements
from invoke.tasks import task
from pathlib import Path

ROOT = Path(__file__).parent.absolute()


@task
def test(c):
    c.run(f'cd {ROOT} && {sys.executable} -m pytest tests/ -v')

@task
def build(c):
    c.run(f'rm -rf {ROOT / "dist"}')
    c.run(f'cd {ROOT} && {sys.executable} -m build')

