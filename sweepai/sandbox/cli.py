import os
import yaml
import shlex
import tarfile
import docker
import typer
from pathlib import Path
from rich import console

import pathspec

from tqdm import tqdm
from src.sandbox_local import SandboxContainer
from src.sandbox_utils import Sandbox

app = typer.Typer(name="sweep-sandbox")

console = console.Console()
print = console.print

client = docker.from_env()


def copy_to(container):
    try:
        git_ignore_patterns = [
            line.strip("/")
            for line in open(".gitignore").read().splitlines()
            if not line.startswith("#") and line.strip()
        ]
    except FileNotFoundError:
        git_ignore_patterns = []
    git_ignore_patterns += [".git"]
    spec = pathspec.PathSpec.from_lines(
        pathspec.patterns.GitWildMatchPattern, open(".gitignore").read().splitlines()
    )
    files_to_copy = {
        f
        for f in tqdm(Path(".").rglob("*"), desc="Getting files to copy")
        if f.is_file() and not spec.match_file(f) and not str(f).startswith(".git")
    }

    print("Copying files to container...")
    pbar = tqdm(files_to_copy)
    with tarfile.open("repo.tar", "w") as tar:
        for f in pbar:
            pbar.set_description(f"Copying {f}")
            tar.add(f)
    print("Done copying files into container")

    data = open("repo.tar", "rb").read()
    container.put_archive(".", data)
    os.remove("repo.tar")


def get_sandbox_from_config():
    if os.path.exists("sweep.yaml"):
        config = yaml.load(open("sweep.yaml", "r"), Loader=yaml.FullLoader)
        return Sandbox(**config.get("sandbox", {}))
    else:
        return Sandbox()


@app.command()
def sandbox(file_path: Path):
    print("\nGetting sandbox config...\n", style="bold white on cyan")
    sandbox = get_sandbox_from_config()
    print("Running sandbox with the following settings:\n", sandbox)
    print(f"\nSpinning up sandbox container\n", style="bold white on cyan")
    with SandboxContainer() as container:
        try:
            print(f"[bold]Copying files into sandbox[/bold]")
            copy_to(container)

            def wrap_command(command):
                command = shlex.quote(command.format(file_path=file_path))
                return f"bash -c {command}"

            def summarize_logs(logs):
                output_lines = logs.split("\n")
                if len(output_lines) > 10:
                    return (
                        "\n".join(output_lines[:5])
                        + "\n...\n"
                        + "\n".join(output_lines[-5:])
                    )
                return logs

            def run_command(command):
                print(f"\n[bold]Running `{command}`[/bold]\n")
                exit_code, output = container.exec_run(
                    wrap_command(command), stderr=True
                )
                output = output.decode("utf-8")
                if output:
                    print(summarize_logs(output))
                if exit_code != 0 and not ("prettier" in command and exit_code == 2):
                    raise Exception(output)
                return output

            print("\nRunning installation scripts...", style="bold white on cyan")
            for command in sandbox.install:
                run_command(command)

            print("\nRunning linter scripts...", style="bold white on cyan")
            for command in sandbox.check:
                run_command(command)

            print("Success!", style="bold green")
        except Exception as e:
            print(f"Error: {e}", style="bold red")
            raise e


if __name__ == "__main__":
    app()
