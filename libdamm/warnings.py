# DAMM 
# Copyright (c) 2013 504ENSICS Labs
#
# This file is part of DAMM.
#
# DAMM is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# DAMM is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with DAMM.  If not, see <http://www.gnu.org/licenses/>.
#

#
# This module implements an experimental set of warnings for possibly-malicious
# activity on a system. It currently operates on a per-DAMM-plugin basis, e.g., 
# there are warnings specific to proceses, dlls, or modules. Many of the ideas
# here are from our research, and things we've picked up from the AOMF book
# the Volatility cheat sheet, user contributions, and some extremely useful
# blog posts including https://sysforensics.org/2014/01/lateral-movement.html
# and https://sysforensics.org/2014/01/know-your-windows-processes.html
#
# Many of these warnings are trivial to look for manually, but the point here 
# is to perform amny checks automatically and immediately point out where the
# 'bad' might be. Also aids repeatability and comprehensiveness in analyses.
# Most importantly, it takes (a small) part of the contents of MHL's head and 
# makes it useful to my investigations.
#


import sqlite3
from utils import debug
import plugin
import time   



# Processes that may indicate malicious activity.

def temp(s):
    return ('tmp' in s.lower() or 'temp' in s.lower())


# Name mangling detectors

# issues: might need an lsplit on unknown processes that might have junk attached

# Did we add in letters, e.g., lsass.exe -> lssass.exe
def matching_lcs(a, b):

    # Borrowed from http://rosettacode.org/wiki/Longest_common_subsequence#Python
    a = a.split('.')[0]
    b = b.split('.')[0]
    lengths = [[0 for j in range(len(b)+1)] for i in range(len(a)+1)]
    # row 0 and column 0 are initialized to 0 already
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            if x == y:
                lengths[i+1][j+1] = lengths[i][j] + 1
            else:
                lengths[i+1][j+1] = \
                    max(lengths[i+1][j], lengths[i][j+1])
    # read the substring out from the matrix
    result = ""
    x, y = len(a), len(b)
    while x != 0 and y != 0:
        if lengths[x][y] == lengths[x-1][y]:
            x -= 1
        elif lengths[x][y] == lengths[x][y-1]:
            y -= 1
        else:
            assert a[x-1] == b[y-1]
            result = a[x-1] + result
            x -= 1
            y -= 1
    return result == a


def transpositions(s):

    for i, elem in enumerate(s):
        if i == len(s)-1:
            break
        begin = s[:i]
        end = s[i:]
        # Same letter, no real transposition
        if end[0] == end[1]:
            continue
        trans = "%s%s%s%s" % (begin, end[1], end[0], end[2:])

        yield trans

 
suspicious_processes = ['rar.exe', 'reg.exe', 'sc.exe', 'psexec.exe', 'procdump.exe', 'net.exe', 'at.exe',\
                        'schtask.exe', 'cmd.exe', 'net1.exe', 'netstat.exe', 'systeminfo.exe', 'taskkill.exe',\
                        'tasklist.exe', 'powershell.exe', 'nbtstat.exe', 'xcopy.exe', 'nslookup.exe', 'quser.exe',\
                        'ping.exe', 'ftp.exe', 'bitsadmin.exe', 'route.exe', 'regsvr32.exe', 'makecab.exe']


def process_warnings(procs, envars):

    # Get the actual system root from the system environment variables and the profile for this sample
    profile = [x[1] for x in envars if x[0].lower() == 'profile'][0]
    sysroot = [x[1] for x in envars if x[0].lower() == 'systemroot'][0]

    # A set of known details about processes.

    # The current set of options:
    #
    # pid : a fixed pid for the process (System only)
    # image_path: the path where the executable muust be run from
    # user_account: currently unimplemented
    # parent: the parent process 
    # singleton: there should only be ine instance
    # session: the session for the process
    # prio: the default priority for the process
    # childless: process hsould have no children
    # starts_at_boot: the process should start close to system boot time
    #

    known_processes_XP = {
        'system'        : { 'pid' : 4, 'image_path' : '', 'user_account' : 'Local System', 'parent' : ['None'], 'singleton' : True, 'prio' : '8' },
        'smss.exe'      : {'image_path' : '%s\System32\smss.exe' % sysroot, 'user_account' : ['NT AUTHORITY\SYSTEM'], 'parent' : ['system'], 'singleton' : True, 'session' : '', 'prio' : '11' },
        'lsass.exe'     : {'image_path' : '%s\system32\lsass.exe' % sysroot, 'user_account' : 'Local System', 'parent' : ['winlogon.exe'], 'singleton' : True, 'session' : '0', 'prio' : '9', 'childless' : True, 'starts_at_boot' : True, 'starts_at_boot' : True },
        'winlogon.exe'  : {'image_path' : '%s\system32\winlogon.exe' % sysroot, 'user_account' : 'Local System', 'session' : '0', 'prio' : '13' },
        'csrss.exe'     : {'image_path' : '%s\system32\csrss.exe' % sysroot, 'user_account' : ['NT AUTHORITY\SYSTEM'], 'session' : '0', 'prio' : '13', 'starts_at_boot' : True },
        'services.exe'  : {'image_path' : '%s\system32\services.exe' % sysroot, 'parent' : ['winlogon.exe'], 'session' : '0', 'prio' : '9', 'starts_at_boot' : True },
        'svchost.exe'   : {'image_path' : '%s\System32\svchost.exe' % sysroot, 'user_account' : ['NT AUTHORITY\SYSTEM', 'LOCAL SERVICE', 'NETWORK SERVICE'], 'parent' : ['services.exe'], 'singleton' : False, 'session' : '0', 'prio' : '8', 'starts_at_boot' : True },
        'explorer.exe'  : {'image_path' : '%s\explorer.exe' % sysroot, 'prio' : '8' },
    }

    known_processes_Vista = {
        'system'        : { 'pid' : 4, 'image_path' : '', 'user_account' : 'Local System', 'parent' : ['None'], 'singleton' : True, 'prio' : '8' },
        'smss.exe'      : {'image_path' : '%s\System32\smss.exe' % sysroot, 'user_account' : ['NT AUTHORITY\SYSTEM'], 'parent' : ['System'], 'singleton' : True, 'session' : '', 'prio' : '11' },
        'wininit.exe'   : {'image_path' : '%s\System32\wininit.exe' % sysroot, 'user_account' : ['NT AUTHORITY\SYSTEM'], 'parent' : ['smss.exe'], 'session' : '0', 'children' : False, 'prio' : '13', 'starts_at_boot' : True },
        'lsass.exe'     : {'image_path' : '%s\system32\lsass.exe' % sysroot, 'user_account' : 'Local System', 'parent' : ['wininit.exe'], 'singleton' : True, 'session' : '0', 'prio' : '9', 'childless' : True, 'starts_at_boot' : True },
        'winlogon.exe'  : {'image_path' : '%s\system32\winlogon.exe' % sysroot, 'user_account' : 'Local System', 'session' : '1' , 'prio' : '13'},
        'csrss.exe'     : {'image_path' : '%s\system32\csrss.exe' % sysroot, 'user_account' : ['NT AUTHORITY\SYSTEM'], 'prio' : '13', 'starts_at_boot' : True },
        'services.exe'  : {'image_path' : '%s\system32\services.exe' % sysroot, 'parent' : ['wininit.exe'], 'session' : '0', 'prio' : '9', 'starts_at_boot' : True },
        'svchost.exe'   : {'image_path' : '%s\System32\svchost.exe' % sysroot, 'user_account' : ['NT AUTHORITY\SYSTEM', 'LOCAL SERVICE', 'NETWORK SERVICE'], 'parent' : ['services.exe'], 'singleton' : False, 'session' : '0', 'prio' : '8', 'starts_at_boot' : True },
        'lsm.exe'      : {'image_path' : '%s\System32\lsm.exe' % sysroot, 'user_account' : ['NT AUTHORITY\SYSTEM'], 'parent' : ['wininit.exe'], 'session' : '0', 'prio' : '8', 'childless' : True, 'starts_at_boot' : True },
        'explorer.exe'  : {'image_path' : '%s\explorer.exe' % sysroot, 'prio' : '8' },
    }

    # Differentiate between OSs
    if profile.startswith('WinXP'):
        known_processes = known_processes_XP
    elif profile.startswith('Win7') or profile.startswith('Vista'):
        known_processes = known_processes_Vista
    else:
        known_processes = { }

    # Wrangle some useful data
    procs_by_name = {}
    procs_by_pid = {}
    num_children = {}
    system_start = None
    for elem in procs:
        procs_by_name[elem.fields['name']] = elem
        procs_by_pid[elem.fields['pid']] = elem
        num_children['ppid'] = 1 if not num_children.get('ppid') else num_children['ppid'] + 1
        # We want below to be System, but there is no create_time for System
        if elem.fields['name'].lower() == 'smss.exe':
            system_start = elem.fields['create_time']

    # There are only a few known processes so in order to detect letter 
    # swapping name mangling we'll just build a dict of all possibilities
    known_proc_transpostitons = {}
    for elem in known_processes.keys():
        for t in transpositions(elem.split('.')[0]):
            known_proc_transpostitons[t] = elem


    # 2010-08-11 06:06:39 UTC+0000     
    system_start = time.strptime(system_start.split('UTC')[0].strip(), '%Y-%m-%d %H:%M:%S')

    # Checks for all processes; if the fields are populated, why not?                    
    for elem in procs:

        # Thanks to Barry McIntosh for the idea for this check, and to the 
        # sysforensics blog post "Do not fumble the lateral movement"
        if elem.fields['name'].lower() in suspicious_processes:
            yield "%s (pid: %s) is suspicious (possible info gathering/persistence/lateral movement)." % (elem.fields['name'], elem.fields['pid'])

        # Look for 'temp' or 'tmp' file or directory names
        if temp(elem.fields['image_path_name']):
            yield "%s (pid: %s) image path in temp: %s." % (elem.fields['name'], elem.fields['pid'], elem.fields['image_path_name'])            
        if temp(elem.fields['command_line']):
            yield "%s (pid: %s) command line contains temp: %s." % (elem.fields['name'], elem.fields['pid'], elem.fields['command_line'])

        # Fake exit time, still has threads running, from AOMF    
        if (elem.fields['exit_time'] != '') and (elem.fields['threads'] != '0'):
            yield "%s (pid: %s) has an exit time of %s and but also has %s running threads." % (elem.fields['name'], elem.fields['pid'], elem.fields['exit_time'], elem.fields['threads'])

        # Process unlinked from list    
        # So not in pslist        
        if elem.fields['pslist'] == 'False':
            others = 0 
            for xview in ['psscan', 'thrdproc', 'pspcid', 'csrss', 'session', 'deskthrd']:
                if elem.fields[xview] == 'True':
                    others += 1
            # But in several other lists
            # Is this a reasonable heuristic?
            if others > 3:
                yield "%s (pid: %s) may be a hidden process." % (elem.fields['name'], elem.fields['pid'])

        # Is process disguised to look like a known_process by adding letters or number -> letter swaps?
        for proc_name in known_processes.keys():
            if (elem.fields['name'].lower() != proc_name) and (matching_lcs(proc_name, elem.fields['name'])):
                yield "%s (pid: %s) is named suspiciously similarly to a Windows process: %s." % (elem.fields['name'], elem.fields['pid'], proc_name)
            elif (elem.fields['name'].lower() != proc_name) and (elem.fields['name'].replace('1', 'l').replace('3', 'e').replace('0', 'o').replace('5', 's').lower() == proc_name):
                yield "%s (pid: %s) is named suspiciously similarly to a Windows process: %s." % (elem.fields['name'], elem.fields['pid'], proc_name)

        # Did we just transpose some pair of letters, e.g., csrss.exe -> crsss.exe
        # Must also account for junk appended to end of process name
        if known_proc_transpostitons.get(elem.fields['name'].split('.')[0]):
            yield "%s (pid: %s) is named suspiciously similarly to a Windows process: %s." % (elem.fields['name'], elem.fields['pid'], known_proc_transpostitons.get(elem.fields['name'].split('.')[0]))


    # Checks all for known processes, using constraints dict above        
    for elem in [x for x in procs if x.fields['name'].lower() in known_processes.keys()]:

        # For allocated, running processes only.       
        if elem.fields['pslist'] == 'True' and elem.fields['exit_time'] == '':

            # Get the set of constraints for this known process
            constraints = known_processes[elem.fields['name'].lower()]

            if constraints.get('pid'):
                expected = int(constraints['pid'])
                actual = int(elem.fields['pid'])
                if actual != expected:
                    yield "%s pid expected: %s, actual: %s." % (elem.fields['name'], expected, actual)

            if constraints.get('parent'):
                expected = constraints['parent']
                actual = None
                ppid = elem.fields['ppid']
                #if ppid != '0':
                #    parent = procs_by_pid.get(elem.fields['ppid'])
                #    if parent:
                #        actual = parent.fields['name']
                if ppid != '0':
                    parent = procs_by_pid.get(elem.fields['ppid'])
                    if parent:
                        actual = parent.fields['name']
                if actual and actual.lower() not in [p.lower() for p in expected]:
                    yield "%s (pid: %s) parent process expected: %s, actual: %s." % (elem.fields['name'], elem.fields['pid'], expected, actual)

            if constraints.get('singleton'):
                instances = 0
                for e in procs:
                    if e.fields['name'] == elem.fields['name'] and e.fields['pslist'] == 'True' and e.fields['exit_time'] == '': 
                        instances += 1
                if instances != 1:
                    yield "%s (pid: %s) has %s instances. Only one instance should exist." % (elem.fields['name'], elem.fields['pid'], instances)

            if constraints.get('image_path'):
                expected = constraints['image_path']
                actual = str(elem.fields['image_path_name'])

                if actual != '':
                    if actual.startswith('\\??\\'):
                        actual = actual.lstrip('\\??\\')
                   
                    if actual.startswith('\\SystemRoot'):
                        actual = actual.replace('\\SystemRoot', sysroot)
                      
                    if actual.lower() != expected.lower():
                        yield "%s (pid: %s) image path expected: %s, actual: %s." % (elem.fields['name'], elem.fields['pid'], expected, elem.fields['image_path_name'])

            if constraints.get('session'):        
                expected = constraints['session']
                actual = elem.fields['session_id']
                if actual != expected:
                    yield "%s (pid: %s) session_id expected: %s, actual: %s." % (elem.fields['name'], elem.fields['pid'], expected, actual)

            if constraints.get('prio'):        
                expected = int(constraints['prio'])
                actual = int(elem.fields['prio'])
                if actual != expected:
                    yield "%s (pid: %s) base priority expected: %s, actual: %s." % (elem.fields['name'], elem.fields['pid'], expected, actual)

            # Thanks to Barry McIntosh for the idea for this check
            if constraints.get('childless'):
                if num_children.get(elem.fields['pid']):
                    yield "%s (pid: %s) has %s children where none were expected." % (elem.fields['name'], elem.fields['pid'], num_children.get(elem.fields['pid']))

            # Thanks to Barry McIntosh for the idea for this check        
            if constraints.get('starts_at_boot'):
                start_time = time.strptime(elem.fields['create_time'].split('UTC')[0].strip(), '%Y-%m-%d %H:%M:%S')
                delta = time.mktime(start_time) - time.mktime(system_start)
                # Is this a reasonable heauristic?
                if delta > 60:
                    #yield "%s (pid: %s) started %s, long after the machine booted at %s." % (elem.fields['name'], elem.fields['pid'], elem.fields['create_time'], str(system_start))
                    yield "%s (pid: %s, %s) started %s seconds after boot time which may be suspicious." % (elem.fields['name'], elem.fields['pid'], elem.fields['command_line'], delta)

 

def injection_warnings(injects, envars):

    # Look for MZ in 'content' - only searches first few bytes
    for inject in injects:
        if 'MZ' in inject.fields['content']:
            yield "%s (pid: %s) has PE header in injection." % (inject.fields['task_image_file_name'], inject.fields['task_unique_proces_id'])


def dll_warnings(dlls, envars):

    for dll in dlls:

        # Is dll run from temp directory?
        if temp(dll.fields['dll_mapped_path']):
            yield "%s (pid: %s) has temp in dll_mapped_path." % (dll.fields['dll_mapped_path'], dll.fields['process_id'])
        if temp(dll.fields['load_full_dll_name']):
            yield "%s (pid: %s) has temp in load_full_dll_name." % (dll.fields['load_full_dll_name'], dll.fields['process_id'])
        if temp(dll.fields['init_full_dll_name']):
            yield "%s (pid: %s) has temp in init_full_dll_name." % (dll.fields['init_full_dll_name'], dll.fields['process_id'])
        if temp(dll.fields['mem_full_dll_name']):
            yield "%s (pid: %s) has temp in mem_full_dll_name." % (dll.fields['mem_full_dll_name'], dll.fields['process_id'])

        # No name or extension or bad extension
        pieces = dll.fields['dll_mapped_path'].rsplit('.', 1)
        if len(pieces) < 2:
            yield "%s (pid: %s) has no extension." % (dll.fields['dll_mapped_path'], dll.fields['process_id'])
        elif dll.fields['dll_mapped_path'].rsplit('.', 1)[1].lower() in ['d1l', 'dl1', 'd11']:
            yield "%s (pid: %s) has a 1 (one) in the extension." % (dll.fields['dll_mapped_path'], dll.fields['process_id'])

        # Hidden dlls
        if dll.fields['dll_mapped_path'].lower().endswith('.dll'):
            if not (dll.fields['dll_in_load'] == 'True') and (dll.fields['dll_in_init'] == 'True') and (dll.fields['dll_in_mem'] == 'True'):
                yield "%s (pid: %s) may be hidden." % (dll.fields['dll_mapped_path'], dll.fields['process_id'])
 

def sid_warnings(sids, envars):

    # Do we see domain privs? This should maybe just look for domain/enterprise admin? From Volatility cheat sheet.
    for sid in sids:
        if 'domain' in sid.fields['sid_name'].lower() or 'enterprise' in sid.fields['sid_name'].lower():
            yield "%s (pid: %s) has %s rights." % (sid.fields['filename'], sid.fields['process_id'], sid.fields['sid_name'])


def handle_warnings(handles, envars):

    # Does the process have a raw socket handles? From Volatility cheat sheet.
    for handle in handles:
        if '\\Device\\RawIp\\0' in handle.fields['name']:
            yield "%s (pid: %s) has a raw socket handle." % (sid.fields['filename'], sid.fields['process_id'], sid.fields['sid_name'])


def privilege_warnings(privs, envars):

    # Does the process have explicitly enabled debug privileges? From Volatility cheat sheet.
    for priv in privs:
        if 'debug' in priv.fields['privilege'].lower():
            if priv.fields['present'] == 'True' and  priv.fields['enabled'] == 'True' and priv.fields['the_default'] == 'False':
                yield "%s (pid: %s) has privilege %s present and enabled, not default." % (priv.fields['filename'], priv.fields['process_id'], priv.fields['privilege'])


def mftentry_warnings(mftentries, envars):

    for entry in mftentries:
        # Check for alternate data streams
        if "DATA ADS" in entry.fields['name']:
            yield "File: %s has an ADS." % (entry.fields['name'])
        # Check for suspicous processes in prefetch
        if entry.fields['name'].lower().endswith('pf'):
            for elem in suspicious_processes:
                if elem.lower() in  entry.fields['name'].lower():
                    yield "%s is a prefetch entry for a suspicious process." % (entry.fields['name'])


def callback_warnings(callbacks, envars):

    # From Volatility cheat sheet.
    for cb in callbacks:
        if 'unknown' in cb.fields['module'].lower():
            yield "Possible malicious callback: %s %s %s %s." % (cb.fields['type'], cb.fields['callback'], cb.fields['module'], cb.fields['detail'])


def timer_warnings(timers, envars):

    # From Volatility cheat sheet.
    for timer in timers:
        if 'unknown' in timer.fields['module'].lower():
            yield "Possible malicious timer: %s %s %s %s %s." % (timer.fields['due_time'], timer.fields['period'], timer.fields['signaled'], timer.fields['routine'], timer.fields['module'])


def module_warnings (modules, envars):

    # Is dll run from temp directory?
    for mod in modules:
        if temp(mod.fields['full_dll_name']):
            yield "Module %s has temp in path." % (mod.fields['full_dll_name'])


def check_warnings(plugins, db):
    
    import db_ops
    db_ops = db_ops.DBOps()

    yield "\nWarnings: (Experimental)"
    
    # Get table names from db
    tables = db_ops.get_tables(db)

    # Get profile from db
    envars = db_ops.get_meta(db)

    # For each table in the db
    for table in tables:

        if table == 'META':
            continue

        # Get the plugin name and setobj name for this db table 
        plug_name, setobj_name = table.split('_')
        # Import the plugin module
        plug = __import__(plug_name)
        # Get a setobj object
        setobj = getattr(plug, setobj_name)()

        # Get the db rows for this table back into objects
        memobjs = []
        for row in db_ops.get_rows(db, table):
            memobjs.append(setobj.memobj_from_row(row))

        # If we have warnings to check, then do it    
        if plug_name in memobj_warning_funcs.keys():
            yield "\nChecking: %s" % plug_name
            for elem in memobj_warning_funcs[plug_name](memobjs, envars):
                yield elem    
    
    yield "\nDone."


memobj_warning_funcs = { 'processes' : process_warnings,
                        'injections' : injection_warnings,
                        'dlls'       : dll_warnings,
                        'privileges' : privilege_warnings,
                        'sids'       : sid_warnings,
                        'handles'    : handle_warnings,
                        'mftentries' : mftentry_warnings,
                        'callback'   : callback_warnings,
                        'timers'     : timer_warnings, 
                        'modules'    : module_warnings }


# Name mangling detectors

# Did we add in letters, e.g., lsass.exe -> lssass.exe
def matching_lcs(a, b):

    # Borrowed from http://rosettacode.org/wiki/Longest_common_subsequence#Python
    a = a.split('.')[0]
    b = b.split('.')[0]
    lengths = [[0 for j in range(len(b)+1)] for i in range(len(a)+1)]
    # row 0 and column 0 are initialized to 0 already
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            if x == y:
                lengths[i+1][j+1] = lengths[i][j] + 1
            else:
                lengths[i+1][j+1] = \
                    max(lengths[i+1][j], lengths[i][j+1])
    # read the substring out from the matrix
    result = ""
    x, y = len(a), len(b)
    while x != 0 and y != 0:
        if lengths[x][y] == lengths[x-1][y]:
            x -= 1
        elif lengths[x][y] == lengths[x][y-1]:
            y -= 1
        else:
            assert a[x-1] == b[y-1]
            result = a[x-1] + result
            x -= 1
            y -= 1
    return result == a


def transpositions(s):

    for i, elem in enumerate(s):
        if i == len(s)-1:
            break
        begin = s[:i]
        end = s[i:]
        # Same letter, no real transposition
        if end[0] == end[1]:
            continue
        trans = "%s%s%s%s" % (begin, end[1], end[0], end[2:])

        yield trans


def temp(s):
    return ('tmp' in s.lower() or 'temp' in s.lower())
