import os
from logging import getLogger
from pathlib import Path

from git import InvalidGitRepositoryError, Repo

from .brick import Brick
from .defaults import Defaults

log = getLogger(__name__)


CONFIG_PATH = Path(__file__).parent / "global_git_config"


class Colours:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    END_C = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


def set_home():
    """
    Set home in order to override git global confg settings
    """
    os.environ["HOME"] = str(CONFIG_PATH)


# noinspection PyBroadException
def compare_changes(defaults: Defaults, pmacs) -> None:
    try:
        set_home()
        git_repo = Repo(defaults.backup_folder)

        paths = "*" + defaults.positions_suffix
        diff = git_repo.index.diff(None, create_patch=True, paths=paths)

        output = "\n --------- Motor Position Changes ----------"
        file_out = "\n --------- Mxx62 differences ----------"
        for d in diff:
            if d.a_blob is not None:
                name = f"{d.a_blob.path}"
                name = Path(name).name
                name = name.replace(defaults.positions_suffix, "")
                if not pmacs or name in pmacs:
                    if d.diff is not None:
                        # d.diff can be either a string or bytes, depending on the context
                        # If already a string, it will remain unchanged
                        # If it's bytes, it will be decoded to a string
                        patch = d.diff
                        if isinstance(patch, bytes):
                            patch = patch.decode("utf8")
                        count_diffs = Brick.diff_to_counts(name, patch, defaults)
                        if count_diffs != "":
                            output += f"\n{name}\n{count_diffs}"
                        file_out += f"\n{name}\n{patch}"

        if len(diff) == 0:
            output += "\nThere are no changes to positions since the last commit"
        filepath = defaults.motion_folder / defaults.positions_file
        with filepath.open("w") as f:
            f.write(f"{output}\n{file_out}")

        # commit the most recent positions comparison for a record of
        # where motors had moved to before the restore
        comparison_file = str(defaults.motion_folder / defaults.positions_file)
        git_repo.index.add([comparison_file])
        git_repo.index.commit(
            "commit of positions comparisons by dls-backup-bl",
        )

        print(f"{Colours.FAIL}{output}{Colours.END_C}")

    except BaseException:
        msg = "ERROR: Repository positions comparison failed."
        log.critical(msg)
        log.debug(msg, exc_info=True)


# noinspection PyBroadException
def commit_changes(defaults: Defaults, do_positions=False):
    # Link to beamline backup git repository in the motion area
    try:
        set_home()

        try:
            git_repo = Repo(defaults.backup_folder)
        except InvalidGitRepositoryError:
            log.error("There is no git repo - creating a repo")
            git_repo = Repo.init(defaults.backup_folder)

        # Gather up any changes
        untracked_files = git_repo.untracked_files
        modified_files = [
            diff.a_path
            for diff in git_repo.index.diff(None)
            if diff.change_type == "M" and diff.a_path is not None
        ]

        ignores = [defaults.log_file.name]
        if not do_positions:
            ignores.append(defaults.positions_suffix)
        # dont commit the debug log or motor positions from a recent comparison
        for ignore in ignores:
            untracked_files = [i for i in untracked_files if ignore not in i]
            modified_files = [i for i in modified_files if ignore not in i]

        change_list = untracked_files + modified_files

        # If there are changes, commit them
        if change_list:
            if untracked_files:
                log.info("The following files are untracked:")
                for File in untracked_files:
                    log.info("\t" + File)
            if modified_files:
                log.info("The following files are modified or deleted:")
                for File in modified_files:
                    log.info("\t" + File)

            git_repo.index.add(change_list)
            git_repo.index.commit("commit of devices backup by dls-backup-bl")
            log.critical("Committed changes")
        else:
            log.critical("No changes since last backup")

        # finally make sure the group can write to everything you created
        # this will fail on files not owned by the user so eat stderr
        os.system(f"chmod -R g+w {defaults.backup_folder} 2> /dev/null")

    except BaseException:
        msg = "ERROR: repository not updated"
        log.debug(msg, exc_info=True)
        log.error(msg)
    else:
        log.warning("SUCCESS: _repo changes committed")


# noinspection PyBroadException
def restore_positions(defaults: Defaults):
    try:
        set_home()

        git_repo = Repo(defaults.backup_folder)
        cli = git_repo.git

        # restore the last committed motor positions
        cli.checkout(
            "master", str(defaults.motion_folder) + "/*" + defaults.positions_suffix
        )

    except BaseException:
        msg = "ERROR: Repository positions restore failed."
        log.critical(msg)
        log.debug(msg, exc_info=True)
