from btcp.btcp_socket import BTCPSocket, BTCPStates
from btcp.lossy_layer import LossyLayer
from btcp.constants import *

import io
import time
from queue import Queue

class BTCPClientSocket(BTCPSocket):
    """bTCP client socket
    A client application makes use of the services provided by bTCP by calling
    connect, send, shutdown, and close.

    You're implementing the transport layer, exposing it to the application
    layer as a (variation on) socket API.

    To implement the transport layer, you also need to interface with the
    network (lossy) layer. This happens by both calling into it
    (LossyLayer.send_segment) and providing callbacks for it
    (BTCPClientSocket.lossy_layer_segment_received, lossy_layer_tick).

    Your implementation will operate in two threads, the network thread,
    where the lossy layer "lives" and where your callbacks will be called from,
    and the application thread, where the application calls connect, send, etc.
    This means you will need some thread-safe information passing between
    network thread and application thread.
    Writing a boolean or enum attribute in one thread and reading it in a loop
    in another thread should be sufficient to signal state changes.
    Lists, however, are not thread safe, so to pass data and segments around
    you probably want to use Queues, or a similar thread safe collection.
    """

    def __init__(self, window, timeout):
        """Constructor for the bTCP client socket. Allocates local resources
        and starts an instance of the Lossy Layer.

        You can extend this method if you need additional attributes to be
        initialized, but do *not* call connect from here.
        """
        super().__init__(window, timeout)
        self._lossy_layer = LossyLayer(self, CLIENT_IP, CLIENT_PORT, SERVER_IP, SERVER_PORT)

        # Global state of the program
        self.state = BTCPStates.CLOSED

        # Mutex variable that is locked by the different parts of this program, and unlocked upon receiving a correct response
        self.mutex = True

        # Sequence and acknoledgement numbers
        self.sequence_number = 0
        self.ack_number = 0
        self.acked_until = 0

        # windowsize
        self.windowsize = 0

        # acks
        self.previous_ack = 0
        self.same_ack_times = 0

        # Send buffer
        self.send_buffer = Queue()
        self.unacked_list = []


    ###########################################################################
    ### The following section is the interface between the transport layer  ###
    ### and the lossy (network) layer. When a segment arrives, the lossy    ###
    ### layer will call the lossy_layer_segment_received method "from the   ###
    ### network thread". In that method you should handle the checking of   ###
    ### the segment, and take other actions that should be taken upon its   ###
    ### arrival.                                                            ###
    ###                                                                     ###
    ### Of course you can implement this using any helper methods you want  ###
    ### to add.                                                             ###
    ###########################################################################


    def sendAllSegements(self):
        while( len(self.unacked_list) < self.windowsize):
            segment = self.send_buffer.get()
            self._lossy_layer.send_segment(segment)
            self.unacked_list.append(segment)

            sequence_number, acknowledgement_number, flags, window, data_length, checksum= super().unpack_segment_header(segment[:10])
            #print(f"sent {sequence_number}")   

    def next_sequence_nr(self, sequence_nr):
        if sequence_nr < 65534:
            return sequence_nr+1
        else:
            return 0

    def handle_triple_ack(self, acknowledgement_number):
        if (acknowledgement_number == self.previous_ack):
            self.same_ack_times += 1
        else:
            self.previous_ack = acknowledgement_number
            self.same_ack_times = 1

        if (self.same_ack_times == 3):
            sequence_number, acknowledgement_number, flags, window, data_length, checksum= super().unpack_segment_header(self.unacked_list[0][:10])
            #print(f"resend {sequence_number} because triple ack")
            self._lossy_layer.send_segment(self.unacked_list[0])
            
            self.same_ack_times = 0

    def lossy_layer_segment_received(self, segment):           
        """Called by the lossy layer whenever a segment arrives.

        Things you should expect to handle here (or in helper methods called
        from here):
            - checksum verification (and deciding what to do if it fails)
            - receiving syn/ack during handshake
            - receiving ack and registering the corresponding segment as being
              acknowledged
            - receiving fin/ack during termination
            - any other handling of the header received from the server

        Remember, we expect you to implement this *as a state machine!*
        """

        # The unpacking of the segment message and the segment header
        message = segment[0]
        sequence_number, acknowledgement_number, flags, window, data_length, checksum= super().unpack_segment_header(message[:10])

        # Get the flags into a 3 character string
        flag_bits = "{0:3b}".format(flags)

        # STATE MACHINE

        if (self.state == BTCPStates.SYN_SENT):
            # If ACK and SYN are set
            if (flag_bits[0] == "1" and flag_bits[1] == "1"):
                # Unlock the thread
                self.mutex = True
                # Update ACK_client and adjust windowsize
                self.ack_number = acknowledgement_number
                self.windowsize = window
                #Send segments after handshake is done
                self.sendAllSegements()

        elif (self.state == BTCPStates.ESTABLISHED):
            # IF ACK is set
            if (flag_bits[1] == "1"):
                #print(f"received ack nr {acknowledgement_number} with current own ack = {self.ack_number}")
                # If ACK_server > ACK_client
                if (acknowledgement_number >= self.next_sequence_nr(self.ack_number)):
                    # Remove those that can be removed and update ACK_client
                    self.unacked_list = self.unacked_list[(acknowledgement_number - self.ack_number):]
                    self.ack_number=acknowledgement_number

                #else:
                    # Otherwise we can't use it. Make sure at most 3 same ACKS
                    #self.handle_triple_ack(acknowledgement_number)

                self.sendAllSegements()

        elif (self.state == BTCPStates.FIN_SENT):
            # if message states that both FIN and ack, then we go to state BTCPStates.CLOSED and we send ACK.
            if (flag_bits[1] == "1" and flag_bits[2] == "1"):
                self.mutex = True


    def lossy_layer_tick(self):
        """Called by the lossy layer whenever no segment has arrived for
        TIMER_TICK milliseconds. Defaults to 100ms, can be set in constants.py.

        NOTE: Will NOT be called if segments are arriving; do not rely on
        simply counting calls to this method for an accurate timeout. If 10
        segments arrive, each 99 ms apart, this method will NOT be called for
        over a second!

        The primary use for this method is to be able to do things in the
        "network thread" even while no segments are arriving -- which would
        otherwise trigger a call to lossy_layer_segment_received.

        For example, checking for timeouts on acknowledgement of previously
        sent segments -- to trigger retransmission -- should work even if no
        segments are being received. Although you can't count these ticks
        themselves for the timeout, you can trigger the check from here.

        You will probably see some code duplication of code that doesn't handle
        the incoming segment among lossy_layer_segment_received and
        lossy_layer_tick. That kind of duplicated code would be a good
        candidate to put in a helper method which can be called from either
        lossy_layer_segment_received or lossy_layer_tick.
        """
        
        # STATE MACHINE
        if (self.state == BTCPStates.SYN_SENT):
            SYN = super().build_segment_header(
                            self.sequence_number, self.ack_number,
                            syn_set=True, ack_set=False, fin_set=False,
                            window=0x01, length=0, checksum=0)

            self._lossy_layer.send_segment(SYN)

        elif (self.state == BTCPStates.FIN_SENT):
            FIN = super().build_segment_header(
                            self.sequence_number, self.ack_number,
                            syn_set=False, ack_set=False, fin_set=True,
                            window=0x01, length=0, checksum=0)
            self._lossy_layer.send_segment(FIN)

        elif (self.state == BTCPStates.ESTABLISHED):
            
            # If timeout, resend oldest package, if we have one
            if( len(self.unacked_list) > 0):
                self._lossy_layer.send_segment(self.unacked_list[0])     

                sequence_number, acknowledgement_number, flags, window, data_length, checksum= super().unpack_segment_header(self.unacked_list[0][:10])
                #print(f"Resent package {sequence_number}")         
 
    ###########################################################################
    ### You're also building the socket API for the applications to use.    ###
    ### The following section is the interface between the application      ###
    ### layer and the transport layer. Applications call these methods to   ###
    ### connect, shutdown (disconnect), send data, etc. Conceptually, this  ###
    ### happens in "the application thread".                                ###
    ###                                                                     ###
    ### You *can*, from this application thread, send segments into the     ###
    ### lossy layer, i.e. you can call LossyLayer.send_segment(segment)     ###
    ### from these methods without ensuring that happens in the network     ###
    ### thread. However, if you do want to do this from the network thread, ###
    ### you should use the lossy_layer_tick() method above to ensure that   ###
    ### segments can be sent out even if no segments arrive to trigger the  ###
    ### call to lossy_layer_input. When passing segments between the        ###
    ### application thread and the network thread, remember to use a Queue  ###
    ### for its inherent thread safety.                                     ###
    ###                                                                     ###
    ### Note that because this is the client socket, and our (initial)      ###
    ### implementation of bTCP is one-way reliable data transfer, there is  ###
    ### no recv() method available to the applications. You should still    ###
    ### be able to receive segments on the lossy layer, however, because    ###
    ### of acknowledgements and synchronization. You should implement that  ###
    ### above.                                                              ###
    ###########################################################################

    def connect(self):
        """Perform the bTCP three-way handshake to establish a connection.

        connect should *block* (i.e. not return) until the connection has been
        successfully established or the connection attempt is aborted. You will
        need some coordination between the application thread and the network
        thread for this, because the syn/ack from the server will be received
        in the network thread.

        Hint: assigning to a boolean or enum attribute in thread A and reading
        it in a loop in thread B (preferably with a short sleep to avoid
        wasting a lot of CPU time) ensures that thread B will wait until the
        boolean or enum has the expected value. We do not think you will need
        more advanced thread synchronization in this project.
        """

        # Syn package is created
        SYN = super().build_segment_header(
                            self.sequence_number, self.ack_number,
                            syn_set=True, ack_set=False, fin_set=False,
                            window=0x01, length=0, checksum=0)

        # Update state, send package and lock the mutex
        self.state = BTCPStates.SYN_SENT
        self._lossy_layer.send_segment(SYN)
        self.mutex = False

        # Wait for synack by server
        while (self.mutex == False):
            continue

        # Ack package is created
        ACK = super().build_segment_header(
                            self.sequence_number, self.ack_number,
                            syn_set=False, ack_set=True, fin_set=False,
                            window=0x01, length=0, checksum=0)

        # Update state and send package
        self.state = BTCPStates.ESTABLISHED
        self._lossy_layer.send_segment(ACK)

        # Show user that the client has connected.
        print("client connected")

    def send(self, data):
        """Send data originating from the application in a reliable way to the
        server.

        This method should *NOT* block waiting for acknowledgement of the data.


        You are free to implement this however you like, but the following
        explanation may help to understand how sockets *usually* behave and you
        may choose to follow this concept as well:

        The way this usually works is that "send" operates on a "send buffer".
        Once (part of) the data has been successfully put "in the send buffer",
        the send method returns the number of bytes it was able to put in the
        buffer. The actual sending of the data, i.e. turning it into segments
        and sending the segments into the lossy layer, happens *outside* of the
        send method (e.g. in the network thread).
        If the socket does not have enough buffer space available, it is up to
        the application to retry sending the bytes it was not able to buffer
        for sending.

        Again, you should feel free to deviate from how this usually works.
        """

        counter = 0
        while ( True ):
            
            #Read 1008 bytes into memory
            message = data.read(1008)

            #Exit when we are at the end of the file
            if len(message) == 0: 
                break
            
            # Create segment
            header = super().build_segment_header(
                    self.sequence_number, self.ack_number,
                    syn_set=False, ack_set=False, fin_set=False,
                    window=0x01, length=len(message), checksum=0)

            message = bytearray(message, 'utf-8')

            # Padding
            if( len(message) < 1008 ):
                message = message + b"0"*(1008-len(message) )
            
            segment = io.BytesIO()
            segment.write(header)
            segment.write(message)
            #segment = header + message

            checksum = super().in_cksum(segment.getvalue())
            
            header = super().build_segment_header(
                    self.sequence_number, self.ack_number,
                    syn_set=False, ack_set=False, fin_set=False,
                    window=0x01, length=len(message), checksum=checksum)

            segment = io.BytesIO()
            segment.write(header)
            segment.write(message)

            # Add segment to buffer
            self.send_buffer.put(segment.getvalue())

            # Increase sequence number
            self.sequence_number = self.next_sequence_nr(self.sequence_number)

        while (self.send_buffer.qsize() > 0):
            time.sleep(0.1)
            continue

    def shutdown(self):
        """Perform the bTCP three-way finish to shutdown the connection.

        shutdown should *block* (i.e. not return) until the connection has been
        successfully terminated or the disconnect attempt is aborted. You will
        need some coordination between the application thread and the network
        thread for this, because the fin/ack from the server will be received
        in the network thread.

        Hint: assigning to a boolean or enum attribute in thread A and reading
        it in a loop in thread B (preferably with a short sleep to avoid
        wasting a lot of CPU time) ensures that thread B will wait until the
        boolean or enum has the expected value. We do not think you will need
        more advanced thread synchronization in this project.
        """

        print("client initiates shutdown")
        # Create FIN and ACK packages
        FIN = super().build_segment_header(
                            self.sequence_number, self.ack_number,
                            syn_set=False, ack_set=False, fin_set=True,
                            window=0x01, length=0, checksum=0)

        ACK = super().build_segment_header(
                            self.sequence_number, self.ack_number,
                            syn_set=False, ack_set=True, fin_set=False,
                            window=0x01, length=0, checksum=0)

        # Update state, send package and lock mutex        
        self.state = BTCPStates.FIN_SENT
        self._lossy_layer.send_segment(FIN)
        self.mutex = False

        # Wait for response from server
        while (self.mutex == False):
            continue

        # Update state and send package
        self.state = BTCPStates.CLOSED
        self._lossy_layer.send_segment(ACK)
        print("client shutdown")


    def close(self):
        """Cleans up any internal state by at least destroying the instance of
        the lossy layer in use. Also called by the destructor of this socket.

        Do not confuse with shutdown, which disconnects the connection.
        close destroys *local* resources, and should only be called *after*
        shutdown.

        Probably does not need to be modified, but if you do, be careful to
        gate all calls to destroy resources with checks that destruction is
        valid at this point -- this method will also be called by the
        destructor itself. The easiest way of doing this is shown by the
        existing code:
            1. check whether the reference to the resource is not None.
            2. if so, destroy the resource.
            3. set the reference to None.
        """
        if self._lossy_layer is not None:
            self._lossy_layer.destroy()
        self._lossy_layer = None


    def __del__(self):
        """Destructor. Do not modify."""
        self.close()
