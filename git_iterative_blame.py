#!/usr/bin/env python3

import argparse
import os
import re
import readline
import subprocess
import sys


#####################
# terminal functions

terminal_code_for_name = {
    'bold': '01',
    'faint': '02',
    'italic': '03',
    'underline': '04',
    'inverse': '07',
    'strike': '20',
    'fg-black': '30',
    'fg-red': '31',
    'fg-green': '32',
    'fg-yellow': '33',
    'fg-blue': '34',
    'fg-magenta': '35',
    'fg-cyan': '36',
    'fg-white': '37',
    'bg-black': '40',
    'bg-red': '41',
    'bg-green': '42',
    'bg-yellow': '43',
    'bg-blue': '44',
    'bg-magenta': '45',
    'bg-cyan': '46',
    'bg-white': '47',
}


terminal_escape = lambda *items: \
        '\x1B[00;' + ';'.join(terminal_code_for_name[n] for n in items) + 'm'


##################
# input functions

def complete_filenames(text, state, filenames, suffix=''):
    matches = [(s + suffix) for s in filenames if s.startswith(text)]
    try:
        return matches[state]
    except IndexError:
        return None


################
# git functions

class Commit(object):
    def __init__(self, h):
        self.hash = subprocess.check_output(['git', 'rev-parse', h],
                                            shell=False).strip().decode('utf-8')
        self.author = None
        self.date = None
        self.file_diffs = {}
        self.commit_message = []

        file_diff_contents = []
        for line in subprocess.check_output(['git', 'show', self.hash],
                                            shell=False).splitlines():
            if not line:
                continue
            line = line.decode('utf-8')

            if line.startswith('commit'):
                assert self.hash == line[7:]
                continue
            if line.startswith('Author:'):
                self.author = line[7:].strip()
                continue
            if line.startswith('Date:'):
                self.date = line[5:].strip()
                continue

            if line.startswith('diff'):
                file_diff_contents.append([])

            if file_diff_contents:
                file_diff_contents[-1].append(line)
            else:
                assert len(line) >= 4, 'bad message line in commit %s (%r)' % (
                        self.hash, line)
                self.commit_message.append(line[4:])

        self.commit_message = '\n'.join(self.commit_message)
        for this_diff_contents in file_diff_contents:
            d = FileDiff(this_diff_contents)
            self.file_diffs[d.filename] = d

    def print_stream(self, stream, highlight_lines={}, context_around_highlights=None):
        stream.write('%s by %s on %s\n' % (self.hash, self.author, self.date))
        stream.write('    ' + self.commit_message.replace('\n', '\n    ') + '\n\n')
        for _, d in sorted(self.file_diffs.items()):
            this_file_highlight_lines = {}
            for (filename, left_num, right_num), highlight_char in highlight_lines.items():
                if filename == d.filename:
                    this_file_highlight_lines[(left_num, right_num)] = highlight_char
            if (context_around_highlights is not None) and (not this_file_highlight_lines):
                continue
            d.print_stream(stream, highlight_lines=this_file_highlight_lines,
                           context_around_highlights=context_around_highlights)
            stream.write("\n")

    def __eq__(self, other):
        return self.hash == other.hash


class FileDiffLine(object):
    def __init__(self, filename, left_line_num, right_line_num, line_contents):
        self.filename = filename
        self.left_line_num = left_line_num
        self.right_line_num = right_line_num
        self.line_contents = line_contents

    def get_margin_data(self):
        if self.left_line_num is None:
            return '%s:     ->%5d' % (self.filename, self.right_line_num)
        elif self.right_line_num is None:
            return '%s:%5d->     ' % (self.filename, self.left_line_num)
        else:
            return '%s:%5d->%5d' % (self.filename, self.left_line_num,
                                    self.right_line_num)

    def print_stream(self, stream, margin_length):
        margin_data = self.get_margin_data()
        format = '%%%ds %%s%%s %%s%%s\n' % margin_length
        if self.left_line_num is None:
            prelude_escape = terminal_escape('bold', 'fg-green')
            action_char = '+'
        elif self.right_line_num is None:
            prelude_escape = terminal_escape('strike', 'fg-red')
            action_char = '-'
        else:
            prelude_escape = ''
            action_char = '|'
        postlude_escape = terminal_escape()
        stream.write(format % (margin_data, prelude_escape, action_char,
                               self.line_contents, postlude_escape))

    def __eq__(self, other):
        return self.filename == other.filename and \
               self.left_line_num == other.left_line_num and \
               self.right_line_num == other.right_line_num and \
               self.line_contents == other.line_contents


class FileDiff(object):
    def __init__(self, lines):
        self.lines = []
        self.filename = None

        parsing_contents = False
        for line in lines:
            if line.startswith('diff'):
                assert not parsing_contents
                continue
            if line.startswith('index'):
                assert not parsing_contents
                continue
            if line.startswith('--- a/') or line.startswith('+++ b/'):
                if not parsing_contents:
                    if self.filename is not None:
                        assert self.filename == line[6:]
                    else:
                        self.filename = line[6:]
                    continue

            position_match = re.match(r'^@@ -([0-9]+),[0-9]+ \+([0-9]+),[0-9]+ @@',
                                      line)
            if position_match:
                parsing_contents = True
                left_line_num = int(position_match.group(1))
                right_line_num = int(position_match.group(2))
                continue

            if parsing_contents:
                if line.startswith(' '):
                    self.lines.append(FileDiffLine(self.filename, left_line_num,
                                                   right_line_num, line[1:].rstrip()))
                    left_line_num += 1
                    right_line_num += 1
                elif line.startswith('-'):
                    self.lines.append(FileDiffLine(self.filename, left_line_num,
                                                   None, line[1:].rstrip()))
                    left_line_num += 1
                elif line.startswith('+'):
                    self.lines.append(FileDiffLine(self.filename, None,
                                                   right_line_num, line[1:].rstrip()))
                    right_line_num += 1
                else:
                    assert False, 'unrecognized line: %r' % line

    def get_line(self, left_num, right_num):
        # TODO: could use binary search or something here
        for l in self.lines:
            if l.left_line_num == left_num and l.right_line_num == right_num:
                return l
        raise KeyError(repr((left_num, right_num)))

    def print_stream(self, stream, highlight_lines={}, context_around_highlights=None):
        if not self.lines:
            stream.write('          (diff is blank; may be a binary file)\n')
            return

        margin_length = max(len(l.get_margin_data()) for l in self.lines)
        for l in self.lines:
            # we assume the number of highlight lines is small, so this won't be
            # too slow
            if context_around_highlights is not None:
                should_show_line = False
                for (left_num, right_num), highlight_char in highlight_lines.items():
                    if (l.left_line_num is not None) and (left_num is not None) and \
                            abs(l.left_line_num - left_num) < context_around_highlights:
                        should_show_line = True
                    if (l.right_line_num is not None) and (right_num is not None) and \
                            abs(l.right_line_num - right_num) < context_around_highlights:
                        should_show_line = True
            else:
                should_show_line = True

            if not should_show_line:
                continue

            highlight_char = highlight_lines.get((l.left_line_num, l.right_line_num))
            if highlight_char:
                stream.write('%8s: ' % highlight_char)
            else:
                stream.write('          ')
            l.print_stream(stream, margin_length)


class LineBlame(object):
    def __init__(self, filename, orig_line_number, current_line_number,
                 commit_hash, line_contents):
        self.filename = filename
        self.orig_line_number = orig_line_number
        self.current_line_number = current_line_number
        self.commit_hash = commit_hash
        self.line_contents = line_contents

    def print_stream(self, stream):
        stream.write('%s %s:(%s/%s) | %s\n' % (self.commit_hash, self.filename,
                self.orig_line_number, self.current_line_number, self.line_contents))


class FileBlame(object):
    def __init__(self, filename, commit_hash=None, use_porcelain=True):
        self.commit_hash = commit_hash
        self.filename = filename
        self.lines = []
        if commit_hash is not None:
            cmd = ['git', 'blame', '-slfn', commit_hash, '--', filename]
        else:
            cmd = ['git', 'blame', '-slfn', filename]
        for line in subprocess.check_output(cmd, shell=False).splitlines():
            line = line.decode('utf-8')
            close_paren = line.find(')')
            commit_hash, filename, orig_line_number, current_line_number = \
                    line[:close_paren].strip().split()
            orig_line_number = int(orig_line_number)
            current_line_number = int(current_line_number)
            line_contents = line[close_paren + 2:]
            self.lines.append(LineBlame(filename, orig_line_number,
                    current_line_number, commit_hash, line_contents))

    def get_line_orig(self, line_num):
        for line in self.lines:
            if line.orig_line_number == line_num:
                return line
        raise KeyError(line_num)

    def get_line_current(self, line_num):
        for line in self.lines:
            if line.current_line_number == line_num:
                return line
        raise KeyError(line_num)

    def print_stream(self, stream):
        for l in self.lines:
            l.print_stream(stream)


#####################
# matching functions

def lines_match(l1, l2, params={}):
    l1_strip = l1.line_contents.strip()
    l2_strip = l2.line_contents.strip()
    if (not l1_strip) and (not l2_strip):
        return True
    if (not l1_strip) or (not l2_strip):
        return False

    prefix = os.path.commonprefix([l1_strip, l2_strip])
    if float(len(prefix)) / len(l1_strip) > params.get('min_prefix_length', 0.8):
        return True
    if float(len(prefix)) / len(l2_strip) > params.get('min_prefix_length', 0.8):
        return True
    return False


########################
# interactive interface

def iterative_blame_interactive(target_filename, target_line_num,
                                commit_hash='HEAD', match_params={},
                                full_diffs=False):

    while True:
        sys.stdout.write('... blame %s -- %s\n' % (commit_hash, target_filename))
        b = FileBlame(target_filename, commit_hash=commit_hash)

        if target_line_num:
            blame_target_line = b.get_line_current(target_line_num)
            line_target_filename = blame_target_line.filename
            if line_target_filename != target_filename:
                target_filename = line_target_filename
            target_commit_hash = blame_target_line.commit_hash
        else:
            blame_target_line = None
            line_target_filename = None
            target_commit_hash = commit_hash

        if target_commit_hash.startswith('^'):
            target_commit_hash = target_commit_hash[1:]  # initial commit
        sys.stdout.write('... show %s\n' % target_commit_hash)
        c = Commit(target_commit_hash)

        all_matches = []
        if target_line_num:
            target_line = c.file_diffs[target_filename].get_line(
                    None, blame_target_line.orig_line_number)
            highlight_lines = {
                (line_target_filename, None, blame_target_line.orig_line_number): 'target',
            }

            for _, d in sorted(c.file_diffs.items()):
                for l in d.lines:
                    if lines_match(l, target_line, params=match_params) and \
                            (l != target_line) and (l.left_line_num is not None):
                        match = (d.filename, l.left_line_num, l.right_line_num)
                        all_matches.append(match)
                        highlight_lines[match] = str(len(all_matches))

        else:
            highlight_lines = {}

        context_around_highlights = None if (full_diffs or not highlight_lines) else 10
        c.print_stream(sys.stdout, highlight_lines=highlight_lines,
                       context_around_highlights=context_around_highlights)

        filenames = ['q', 'f'] + sorted(c.file_diffs.keys())
        readline.set_completer(lambda t, s: complete_filenames(t, s, filenames))

        choice = None
        while choice is None:
            if all_matches:
                sys.stdout.write(('Choose a match to continue (1-%d), enter ' +
                        'another location (file:line), or enter f to see full ' +
                        'diff or q to quit.\n') %
                        len(all_matches))
            else:
                sys.stdout.write('No matches. Enter another location ' +
                        '(file:line), or enter f to see full diff or q to quit.\n')

            choice = input()

            if choice.isdigit():
                choice = int(choice)
                if (choice < 1) or (choice > len(all_matches)):
                    choice = None

            elif choice == 'q':
                return 0

            elif choice == 'f':
                c.print_stream(sys.stdout, highlight_lines=highlight_lines)
                choice = None
                continue

            elif ':' in choice:
                try:
                    fn, line = choice.split(':')
                    line = int(line)
                    choice = (fn, line)
                except ValueError:
                    choice = None

            else:
                choice = None

        # use the left number since we're looking before this commit
        if isinstance(choice, tuple):
            target_filename, target_line_num = choice
        elif isinstance(choice, (int, long)):
            target_filename = all_matches[choice - 1][0]
            target_line_num = all_matches[choice - 1][1]
        else:
            assert False, 'incorrect value for choice: %r' % (choice,)
        commit_hash = c.hash + '^'

        print('=' * 120)
        print('=' * 120)
        print('=' * 120)


##############
# entry point

if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('commit_hash', nargs='?', action='store', default='HEAD',
        help='If given, look at the file as of this commit.')
    parser.add_argument('target_position', action='store',
        help='The file and line (e.g. __init__.py:110) to trace.')
    parser.add_argument('-f, --full-diffs', action='store_true', dest='full_diffs',
        help='By default, we show only portions of diffs around matches. ' +
             'This disables that behavior.')
    args = parser.parse_args()

    # iterative_blame_interactive assumes we run in the repo root, so chdir to
    # it before calling. however, we also need to prepend the filename with the
    # intermediate dirs
    base_dir = subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], shell=False).strip().decode('utf-8')
    current_dir = os.getcwd()
    assert current_dir.startswith(base_dir)
    extra_dirs = current_dir[len(base_dir):].strip('/').split('/')

    if ':' in args.target_position:
        target_filename, target_file_position = args.target_position.split(':')
        target_file_position = int(target_file_position)
    else:
        target_filename = args.target_position
        target_file_position = None

    target_filename = os.path.join(*(extra_dirs + [target_filename]))
    os.chdir(base_dir)

    readline.set_completer(lambda: None)
    readline.parse_and_bind('tab: complete')

    sys.exit(iterative_blame_interactive(target_filename, target_file_position,
                                         commit_hash=args.commit_hash,
                                         full_diffs=args.full_diffs))
