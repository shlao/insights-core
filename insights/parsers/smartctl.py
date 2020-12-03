"""
SMARTctl parsers
================

Classes to parse ``smartctl`` command information.

Parsers provided by this module include:

SMARTctl - command ``/sbin/smartctl -a {device}``
-------------------------------------------------

SCT Error Recovery Control - command ``/sbin/smartctl -l scterc {device}``
--------------------------------------------------------------------------

"""

from insights.core import CommandParser
from insights.core.plugins import parser
from insights.parsers import ParseException

import re
from insights.specs import Specs


@parser(Specs.smartctl)
class SMARTctl(CommandParser):
    """
    Parser for output of ``smartctl -a`` for each drive in system.

    This stores the information from the output of `smartctl` in the
    following properties:

     * ``device`` - the name of the device after /dev/ - e.g. sda
     * ``information`` - the -i info (vendor, product, etc)
     * ``health`` - overall health assessment (-H)
     * ``values`` - the SMART values (-c) - SMART config on drive firmware
     * ``attributes`` - the SMART attributes (-A) - run time data

    For legacy access, these are also available as values in the ``info``
    dictionary property, keyed to their name (i.e. info['device'])

    Each object contains a different device; the shared information for this
    parser in Insights will be one or more devices, so see the example below
    for how to iterate through the available SMARTctl information for each
    device.

    Sample (abbreviated) output::

        smartctl 6.2 2013-07-26 r3841 [x86_64-linux-3.10.0-267.el7.x86_64] (local build)
        Copyright (C) 2002-13, Bruce Allen, Christian Franke, www.smartmontools.org

        === START OF INFORMATION SECTION ===
        Device Model:     ST500LM021-1KJ152
        Serial Number:    W620AT02
        LU WWN Device Id: 5 000c50 07817bb36
        ...

        === START OF READ SMART DATA SECTION ===
        SMART overall-health self-assessment test result: PASSED

        General SMART Values:
        Offline data collection status:  (0x00) Offline data collection activity
                            was never started.
                            Auto Offline Data Collection: Disabled.
        ...

        SMART Attributes Data Structure revision number: 10
        Vendor Specific SMART Attributes with Thresholds:
        ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
          1 Raw_Read_Error_Rate     0x000f   118   099   034    Pre-fail  Always       -       179599704
          3 Spin_Up_Time            0x0003   098   098   000    Pre-fail  Always       -       0
          4 Start_Stop_Count        0x0032   100   100   020    Old_age   Always       -       546
          5 Reallocated_Sector_Ct   0x0033   100   100   036    Pre-fail  Always       -       0
        ...

    Examples:
        >>> drive.device
        '/dev/sda'
        >>> drive.information['Device Model']
        'ST500LM021-1KJ152'
        >>> drive.health
        'PASSED'
        >>> drive.values['Self-test execution status']
        '0'
        >>> drive.attributes['Raw_Read_Error_Rate']['raw_value']
        '179599704'

    """

    _INFO_LINE_STR = r'(?P<key>\w+(?:\s\w+)*):\s+' + \
        r'(?P<value>\S.*?)\s*$'
    _INFO_LINE_RE = re.compile(_INFO_LINE_STR)
    _VALUE_LINE_STR = r'(?P<key>\w[A-Za-z _.-]+):\s+' + \
        r'\(\s*(?P<value>\S.*?)\)'
    _VALUE_LINE_RE = re.compile(_VALUE_LINE_STR)
    _ATTR_LINE_STR = r'^\s*(?P<id>\d+)\s(?P<name>\w+)\s+' + \
        r'(?P<flag>0x[0-9a-fA-F]{4})\s+(?P<value>\d{3})\s+' + \
        r'(?P<worst>\d{3})\s+(?P<threshold>\d{3})\s+' + \
        r'(?P<type>[A-Za-z_-]+)\s+(?P<updated>[A-Za-z_-]+)\s+' + \
        r'(?P<when_failed>\S+)\s+(?P<raw_value>\S.*)$'
    _ATTR_LINE_RE = re.compile(_ATTR_LINE_STR)

    def __init__(self, context):
        filename_re = re.compile(r'smartctl_-a_\.dev\.(?P<device>\w+)$')
        match = filename_re.search(context.path)
        if match:
            self.device = '/dev/' + match.group('device')
        else:
            raise ParseException('Cannot parse device name from path {p}'.format(p=context.path))
        super(SMARTctl, self).__init__(context)

    def parse_content(self, content):
        self.information = {}
        self.health = 'not parsed'
        self.values = {}
        self.attributes = {}
        # hack for persistent line storage in parse_content context -
        # otherwise it gets treated as a local variable within the sub-
        # functions
        self.full_line = ''

        # Parsing using a state machine, sorry.  We use a state variable, and
        # functions to parse lines in each of the different states.  The
        # function returns the state as a result of reading that line, and we
        # look up the parse function out of an array based on the parse state.
        PARSE_FORMATTED_INFO = 0
        PARSE_FREEFORM_INFO = 1
        PARSE_ATTRIBUTE_INFO = 2
        PARSE_COMPLETE = 3
        parse_state = PARSE_FORMATTED_INFO

        # Information section:
        def parse_information(line):
            # Exit parsing information section if we go into the next section
            if line.startswith('=== START OF READ SMART DATA SECTION ==='):
                return PARSE_FREEFORM_INFO
            match = self._INFO_LINE_RE.search(line)
            if match:
                self.information[match.group('key')] = match.group('value')
            else:
                # Translate some of the less structured information
                if line == 'Device does not support SMART':
                    self.information['SMART support is'] = 'Not supported'
                elif line == 'Device supports SMART and is Enabled':
                    self.information['SMART support is'] = 'Enabled'
                elif line == 'Error Counter logging not supported':
                    self.information['Error Counter logging'] = \
                        'Not supported'
                elif line == 'Device does not support Self Test logging':
                    self.information['Self Test logging'] = 'Not supported'
                elif line == 'Temperature Warning Disabled or Not Supported':
                    self.information['Temperature Warning'] = \
                        'Disabled or Not Supported'
            return PARSE_FORMATTED_INFO

        # Values section:
        def parse_values(line):
            if line.startswith('Vendor Specific SMART Attributes with Thres'):
                return PARSE_ATTRIBUTE_INFO
            if line.startswith('SMART overall-health self-assessment test r'):
                self.health = ''.join((line.split(': '))[1:])
                return PARSE_FREEFORM_INFO
            # Values section begins with this - ignore:
            if line.startswith('General SMART Values:'):
                return PARSE_FREEFORM_INFO

            # Lines starting with a space are continuations of the commentary
            # on the previous setting - ignore
            if len(line) == 0 or line[0] == ' ' or line[0] == "\t":
                return PARSE_FREEFORM_INFO
            # Otherwise, join this line to the full line
            if self.full_line:
                self.full_line += ' '
            self.full_line += line.strip()

            match = self._VALUE_LINE_RE.search(self.full_line)
            if match:
                # Handle the recommended polling time lines, which are joined
                # with the previous line and values are in minutes.
                (key, value) = match.group('key', 'value')
                self.values[key] = value
                self.full_line = ''
            elif self.full_line.startswith('SMART Attributes Data Structure revision number: '):
                (key, value) = self.full_line.split(': ')
                self.values[key] = value
                self.full_line = ''
            return PARSE_FREEFORM_INFO

        # Attributes sections
        def parse_attributes(line):
            if line.startswith('SMART Error Log Version:'):
                return PARSE_COMPLETE
            if len(line) == 0:
                return PARSE_ATTRIBUTE_INFO
            match = self._ATTR_LINE_RE.match(line)
            if match:
                name = match.group('name')
                self.attributes[name] = match.groupdict()
            return PARSE_ATTRIBUTE_INFO

        parse_for_state = [
            parse_information,
            parse_values,
            parse_attributes,
        ]

        for line in content:
            parse_state = parse_for_state[parse_state](line)
            if parse_state == PARSE_COMPLETE:
                break

        # Delete temporary full line storage
        del self.full_line


@parser(Specs.smartctl_l_scterc)
class SMARTctlSCTERC(CommandParser, dict):
    """
    Parser for output of ``smartctl -l scterc`` for each drive in system.

    This stores the SCT ERC (Smart Command Transfer Error Recovery Control) information
    from the output of `smartctl -l scterc` in the following properties:
    following properties:

    * ``device`` - the name of the device after /dev/ - e.g. sda

    Sample output::

        smartctl 7.1 2020-04-05 r5049 [x86_64-linux-4.18.0-240.el8.x86_64] (local build)
        Copyright (C) 2002-19, Bruce Allen, Christian Franke, www.smartmontools.org
        SCT Error Recovery Control set to:
         Read: 200 (20.0 seconds)
         Write: 200 (20.0 seconds)

    Examples:
        >>> scterc.device
        '/dev/sda'
        >>> scterc['Read']
        20.0
        >>> scterc['Write']
        20.0

    """

    def __init__(self, context):
        filename_re = re.compile(r'smartctl_-l_scterc_\.dev\.(?P<device>\w+)$')
        match = filename_re.search(context.path)
        if match:
            self.device = '/dev/' + match.group('device')
        else:
            raise ParseException('Cannot parse device name from path {p}'.format(p=context.path))
        super(SMARTctlSCTERC, self).__init__(context)

    def parse_content(self, content):
        key_values = [l.split()[:2] for l in content if "Read:" in l or "Write:" in l]

        for key, val in key_values:
            if val.isdigit():
                val = float(int(val) / 10)

            self[key[:-1]] = val
