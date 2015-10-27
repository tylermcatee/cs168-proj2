import sys
import getopt
import random

import Checksum
import BasicSender

# From @633 
# "You can use any initial sequence number. Don't have to be random"
# Decided to use my favorite number
INITIAL_SEQUENCE_NUMBER = 725
# For being a placeholder until we determine what our final sequence number will be
FINAL_SEQUENCE_NUMBER_PLACEHOLDER = -1

# Define my message types as constants
MSG_TYPE_SYN = 'syn'
MSG_TYPE_DAT = 'dat'
MSG_TYPE_FIN = 'fin'
MSG_TYPE_ACK = 'ack'

# Define any special message bodies as constants
PACKET_BODY_EMPTY = ''

# The sender should implement a 500ms retransmission timer to automatically
# retransmit packets that were never acknowledged (potentially due to ackpackets
# being lost). We do not expect you to use an adaptive timeout.
RETRANSMISSION_TIME = 0.5 # 500ms = .5s

# For dissecting the packet
PACKET_DELIMITER = "|"
PACKET_COMPONENT_MSG_TYPE = 0
PACKET_COMPONENT_SEQUENCE_NUMBER = 1
PACKET_COMPONENT_CHECKSUM = 2

# in order to meet the performance requirement, packet size should be larger than 
# 1000 bytes (unless it is the last packet in the stream), but less than 1472 bytes.
PACKET_SIZE = 1471

# Your sender should support a window size of 7 packets (i.e., 7 unacknowledged
# packets).
WINDOW_SIZE = 7

# Fast retransmit constants
FAST_RETRANSMIT_MAX_DUPLICATIONS = 4

'''
This is a skeleton sender class. Create a fantastic transport protocol here.
'''
class Sender(BasicSender.BasicSender):
    def __init__(self, dest, port, filename, debug=False, sackMode=False):
        super(Sender, self).__init__(dest, port, filename, debug)
        self.sackMode = sackMode
        self.debug = debug

        # To initiate a connection, send a synmessage with a random initial sequence number.
        self.sequence_number = INITIAL_SEQUENCE_NUMBER

        # Keep track of the current window of packets being sent out to the receiver
        self.window = []
        # To know the base value of our window currently
        self.window_sequence_number = INITIAL_SEQUENCE_NUMBER
        # To know when our sequence is finished.
        self.final_sequence_number = FINAL_SEQUENCE_NUMBER_PLACEHOLDER

        # Packet body
        self.packet_body = PACKET_BODY_EMPTY

        # For fast transmit
        self.fast_retransmit_counter = 0

    # Main sending loop.
    def start(self):
        # First step in the BEARS-TP protocol is to do a hand shake
        self.hand_shake()
        # Get the body of data to send using the packet size
        self.packet_body = self.infile.read(PACKET_SIZE)
        # Second step is to transmit the actual data
        self.transmit_data()
        # Done

    def packet_components(self, packet):
        return packet.split(PACKET_DELIMITER)

    def hand_shake(self):
        # Create a syn packet with the random initial sequence number
        syn_packet = self.make_packet(MSG_TYPE_SYN, self.sequence_number, PACKET_BODY_EMPTY)

        # Prepare to receive
        ack_packet = None
        ack_msg_type = None
        ack_sequence_number = None

        # We want to populate ack_packet, make sure the response message type is ack,
        # and check that the sequence number is the self.sequence_number + 1
        while not ack_packet and ack_msg_type != MSG_TYPE_ACK and ack_sequence_number != self.sequence_number + 1:
            # Send the packet
            self.send(syn_packet)
            # After sending the synmessage, the sender waits for an ackpacket to finish a handshake
            ack_packet = self.receive(RETRANSMISSION_TIME)

            if ack_packet:
                # Split apart the packet
                components = self.packet_components(ack_packet)
                ack_msg_type = components[PACKET_COMPONENT_MSG_TYPE]
                ack_sequence_number = int(components[PACKET_COMPONENT_SEQUENCE_NUMBER])
                # Validate the checksum
                valid_checksum = Checksum.validate_checksum(ack_packet)
                if not valid_checksum:
                    ack_packet = None


    def transmit_data(self):

        # Fill the window
        while len(self.window) < WINDOW_SIZE and self.packet_body != PACKET_BODY_EMPTY:
            # Get the next packet body so we can see if this one is the final one or not
            next_packet_body = self.infile.read(PACKET_SIZE)
            # Advance the sequence number
            self.sequence_number += 1

            if next_packet_body != PACKET_BODY_EMPTY:
                # The current packet is not the last, full transmission!
                dat_packet = self.make_packet(MSG_TYPE_DAT, self.sequence_number, self.packet_body)
                # Send out the packet
                self.send(dat_packet)
                # Add the packet to our current window
                self.window.append(dat_packet)
            else:
                # The current packet is the LAST!
                fin_packet = self.make_packet(MSG_TYPE_FIN, self.sequence_number, self.packet_body)
                # Send out the packet
                self.send(fin_packet)
                # Add the packet to our current window
                self.window.append(fin_packet)
                # Note that this is our final sequence number
                self.final_sequence_number = self.sequence_number

            # Update the packet_body to next_packet_body
            self.packet_body = next_packet_body

        # Now that we have sent out the window, we want to get the responses
        ack_packet = self.receive(RETRANSMISSION_TIME)

        if ack_packet:
            # Split apart the packet
            components = self.packet_components(ack_packet)
            ack_msg_type = components[PACKET_COMPONENT_MSG_TYPE]
            ack_sequence_number = int(components[PACKET_COMPONENT_SEQUENCE_NUMBER])

            # Only continue if checksum is valid
            valid_checksum = Checksum.validate_checksum(ack_packet)
            if valid_checksum:
                # Check to see if we are done
                if self.sequence_number_is_last(ack_sequence_number):
                    # We are finished, return to the start() method
                    return
                elif self.sequence_number_is_past_window(ack_sequence_number):
                    # Update the window
                    self.move_window_sequence_number(ack_sequence_number)
                elif self.sequence_number_is_at_window(ack_sequence_number):
                    """
                    Go Back N sender retransmits the unacknowledged packet only on a timeout event, 
                    which can wait up to 500ms. Instead, we can detect the packet loss event by 
                    duplicated ack event. If we receive three duplicate acknowledgements (that is, 
                    receiving the same acknowledgement number for 4 times), we are almost sure the 
                    packet has been lost and we should immediately retransmit the packet.
                    """
                    self.fast_retransmit_counter += 1
                    if self.fast_retransmit_counter >= FAST_RETRANSMIT_MAX_DUPLICATIONS:
                        self.fast_retransmit_counter = 0
                        packet = self.packet_for_sequence_number(ack_sequence_number)
                        if packet:
                            self.send(packet)
        else:
            # Need to re-send the entire window!
            self.resend_window()

        # We didn't finish, transmit more data
        self.transmit_data()

    def sequence_number_is_last(self, ack_sequence_number):
        return ack_sequence_number == self.final_sequence_number + 1

    def sequence_number_is_past_window(self, ack_sequence_number):
        return ack_sequence_number >= self.window_sequence_number + 1

    def sequence_number_is_at_window(self, ack_sequence_number):
        return ack_sequence_number == self.window_sequence_number

    def move_window_sequence_number(self, new_window_sequence_number):
        """
        Takes a new window sequence number
        Looks at our current window and removes all packets under the new sequence number
        Advances self.window_sequence_number to new_window_sequence_number
        """
        packets_to_remove = []
        for packet in self.window:
            components = self.packet_components(packet)
            packet_sequence_number = int(components[PACKET_COMPONENT_SEQUENCE_NUMBER])
            if packet_sequence_number < new_window_sequence_number:
                packets_to_remove.append(packet)
        for packet in packets_to_remove:
            self.window.remove(packet)
        self.window_sequence_number = new_window_sequence_number

    def packet_for_sequence_number(self, ack_sequence_number):
        for packet in self.window:
            components = self.packet_components(packet)
            packet_sequence_number = int(components[PACKET_COMPONENT_SEQUENCE_NUMBER])
            if packet_sequence_number == ack_sequence_number:
                return packet
        return None

    def resend_window(self):
        # Reset the transmit counter to 0
        self.fast_retransmit_counter = 0
        # Send every packet again
        for packet in self.window:
            self.send(packet)


        
'''
This will be run if you run this script from the command line. You should not
change any of this; the grader may rely on the behavior here to test your
submission.
'''
if __name__ == "__main__":
    def usage():
        print "BEARS-TP Sender"
        print "-f FILE | --file=FILE The file to transfer; if empty reads from STDIN"
        print "-p PORT | --port=PORT The destination port, defaults to 33122"
        print "-a ADDRESS | --address=ADDRESS The receiver address or hostname, defaults to localhost"
        print "-d | --debug Print debug messages"
        print "-h | --help Print this usage message"
        print "-k | --sack Enable selective acknowledgement mode"

    try:
        opts, args = getopt.getopt(sys.argv[1:],
                               "f:p:a:dk", ["file=", "port=", "address=", "debug=", "sack="])
    except:
        usage()
        exit()

    port = 33122
    dest = "localhost"
    filename = None
    debug = False
    sackMode = False

    for o,a in opts:
        if o in ("-f", "--file="):
            filename = a
        elif o in ("-p", "--port="):
            port = int(a)
        elif o in ("-a", "--address="):
            dest = a
        elif o in ("-d", "--debug="):
            debug = True
        elif o in ("-k", "--sack="):
            sackMode = True

    s = Sender(dest,port,filename,debug, sackMode)
    try:
        s.start()
    except (KeyboardInterrupt, SystemExit):
        exit()
