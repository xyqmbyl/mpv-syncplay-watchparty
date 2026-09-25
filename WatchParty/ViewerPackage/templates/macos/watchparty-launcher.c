/* Finder entry point for a portable WatchParty mpv.app.
 *
 * Keep the upstream mpv binary in place: its libraries and resources use that
 * location. execv retains the LaunchServices process and pending AppleEvents,
 * so mpv continues to handle files opened with Finder or dropped on the app.
 * No shell, user-global configuration, or build-time absolute paths are used.
 */
#include <mach-o/dyld.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static void fail(const char *message)
{
    fprintf(stderr, "WatchParty: %s\n", message);
    exit(EXIT_FAILURE);
}

int main(int argc, char **argv)
{
    uint32_t size = 0;
    _NSGetExecutablePath(NULL, &size);
    char *unresolved = malloc(size);
    if (!unresolved || _NSGetExecutablePath(unresolved, &size) != 0)
        fail("cannot locate application executable");

    char *root = realpath(unresolved, NULL);
    free(unresolved);
    if (!root)
        fail("cannot resolve application path");

    char *slash = strrchr(root, '/');
    if (!slash)
        fail("invalid application path");
    *slash = '\0';
    char *mpv = NULL;
    if (asprintf(&mpv, "%s/mpv", root) < 0)
        fail("out of memory");

    /* MacOS -> Contents -> mpv.app -> extracted package directory. */
    for (int level = 0; level < 3; level++) {
        slash = strrchr(root, '/');
        if (!slash)
            fail("invalid application bundle path");
        *slash = '\0';
    }

    char *config = NULL;
    if (asprintf(&config, "%s/portable_config", root) < 0)
        fail("out of memory");
    struct stat config_stat;
    if (stat(config, &config_stat) != 0 || !S_ISDIR(config_stat.st_mode))
        fail("portable_config is missing beside mpv.app; keep the extracted package together");

    char *config_arg = NULL;
    if (asprintf(&config_arg, "--config-dir=%s", config) < 0)
        fail("out of memory");
    char **mpv_argv = calloc((size_t)argc + 2, sizeof(*mpv_argv));
    if (!mpv_argv)
        fail("out of memory");
    mpv_argv[0] = mpv;
    mpv_argv[1] = config_arg;
    for (int index = 1; index < argc; index++)
        mpv_argv[index + 1] = argv[index];
    /* Retain cwd so relative media arguments still refer to the caller's cwd.
     * Explicit options supplied by the caller follow the portable default. */
    execv(mpv, mpv_argv);
    perror("WatchParty: cannot start bundled mpv");
    return EXIT_FAILURE;
}
