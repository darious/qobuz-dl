from .qopy import Client


def main():
    from .cli import main as cli_main

    return cli_main()
