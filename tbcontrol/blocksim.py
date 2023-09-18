from collections import defaultdict

import numpy
import scipy
import scipy.signal


class Block:
    def __init__(self, name, inputname, outputname):
        self.name = name
        self.inputname = inputname
        self.outputname = outputname

    def __repr__(self):
        return f"{self.__class__.__name__}: {self.inputname} →[ {self.name} ]→ {self.outputname}"


class LTI(Block):
    """Represents a general Linear Time Invariant system with optional delay"""

    def __init__(self, name, inputname, outputname, numerator, denominator=1, delay=0):
        """
        :param name: str, The name of the block
        :param inputname: str, the name of the input signal
        :param outputname: str, the name of the output signal
        :param numerator: number or iterable of numbers for numerator coefficients in descending order
        :param denominator:  number or iterable of numbers for denominator coefficients in descending order
        :param delay: number, delay

        """
        super().__init__(name, inputname, outputname)

        self.G = scipy.signal.lti(numerator, denominator)
        self.Gss = self.G.to_ss()
        if delay > 0:
            self.delay = Deadtime(None, None, None, delay)
        else:
            self.delay = None
        self.reset()

    def reset(self):
        self.change_state(numpy.zeros((self.Gss.A.shape[0], 1)))
        self.y = self.output = 0
        if self.delay:
            self.delay.reset()

    def change_input(self, t, u):
        self.y = (self.Gss.C.dot(self.x) + self.Gss.D.dot(u))[0, 0]
        if self.delay:
            self.y = self.delay.change_input(t, self.y)
        self.output = self.y
        return self.output

    def change_state(self, x):
        self.x = self.state = x

    def derivative(self, e):
        return self.Gss.A.dot(self.x) + self.Gss.B.dot(e)


class Controller(LTI):
    def __init__(
        self,
        name,
        inputname,
        outputname,
        numerator,
        denominator=1,
        delay=0,
        automatic=True,
    ):
        self.automatic = True
        super().__init__(name, inputname, outputname, numerator, denominator, delay)

    def change_input(self, t, u):
        if self.automatic:
            return super().change_input(t, u)
        else:
            return self.output


class PI(Controller):
    def __init__(self, name, inputname, outputname, Kc, tau_i):
        """Textbook PI controller"""
        super().__init__(name, inputname, outputname, [Kc * tau_i, Kc], [tau_i, 0])


class PID(Controller):
    def __init__(self, name, inputname, outputname, Kc, tau_i, tau_d=0, alpha_f=0.1):
        """Standard realisable parallel form ISA PID controller with first order filter.

        If tau_d=0, a PI controller is returned"""

        if tau_d == 0:
            return PI(name, inputname, outputname, Kc, tau_i)

        super().__init__(
            name,
            inputname,
            outputname,
            numerator=[
                Kc * alpha_f * tau_d * tau_i + Kc * tau_d * tau_i,
                Kc * alpha_f * tau_d + Kc * tau_i,
                Kc,
            ],
            denominator=[alpha_f * tau_d * tau_i, tau_i, 0.0],
        )


class Zero(Block):
    def __init__(self, name, inputname, outputname):
        super().__init__(name, inputname, outputname)

    def reset(self):
        self.change_state(0)
        pass

    def change_input(self, t, u):
        return 0

    def change_state(self, x):
        self.x = self.state = 0

    def derivative(self, e):
        return 0


class AlgebraicEquation(Block):
    def __init__(self, name, inputname, outputname, f):
        """Relationship between input and output specified by an external function

        :param f: function(t, u)
        """
        super().__init__(name, inputname, outputname)
        self.f = f
        self.reset()

    def reset(self):
        self.change_state(0)
        self.y = self.output = 0

    def change_input(self, t, u):
        self.y = self.output = self.f(t, u)
        return self.output

    def change_state(self, x):
        self.x = self.state = x

    def derivative(self, e):
        return 0


class Deadtime(Block):
    def __init__(self, name, inputname, outputname, delay):
        super().__init__(name, inputname, outputname)

        self.delay = delay
        self.reset()

    def reset(self):
        self.change_state(0)
        self.y = self.output = 0
        self.ts = [0]
        self.us = [0]

    def change_input(self, t, u):
        self.ts.append(t)
        self.us.append(u)
        if self.delay > 0:
            u = numpy.interp(t - self.delay, self.ts, self.us)

        self.y = u
        self.output = self.y
        return self.output

    def change_state(self, x):
        self.x = self.state = x

    def derivative(self, e):
        return 0


class DiscreteTF(Block):
    def __init__(self, name, input_name, output_name, dt, numerator, denominator):
        """
        Represents a discrete transfer function.

        The TF must be of the form:

                  -1       -2            -n
        b  +  b  z   + b  z  + ... + b  z
         0     1        2             n
        -----------------------------------
                  -1       -2            -m
        a  +  a  z   + a  z  + ... + a  z
         0     1        2             m


        Parameters
        ----------
        dt : float
            The sampling time of the transfer function.
        numerator : array_like
            The numerator coefficient vector in a 1-D sequence.
            [b_0, ..., b_n]
        denominator : array_like
            The denominator coefficient vector in a 1-D sequence.
            [a_0, ..., a_m]; a_0 != 0

        """
        super().__init__(name, input_name, output_name)

        if denominator[0] == 0:
            raise ValueError(
                "The leading coefficient of the denominator cannot be zero"
            )

        self.dt = dt
        self.y_cos = denominator[::-1]
        self.u_cos = numerator[::-1]

        self.ys = numpy.zeros(len(self.y_cos))
        self.us = numpy.zeros(len(self.u_cos))
        self.next_sample = 0

        self.state = 0.0
        self.output = 0.0

    def reset(self):
        self.ys = numpy.zeros(len(self.y_cos))
        self.us = numpy.zeros(len(self.u_cos))
        self.next_sample = 0
        self.state = 0.0
        self.output = 0.0

    def change_input(self, t, u):
        if t > self.next_sample:
            self.next_sample += self.dt

            self.us[:-1] = self.us[1:]
            self.us[-1] = u

            self.ys[:-1] = self.ys[1:]
            self.ys[
                -1
            ] = None  # done to ensure that if anything should go wrong, it does

            u_sum = numpy.inner(self.u_cos, self.us)
            y_sum = numpy.inner(self.y_cos[:-1], self.ys[:-1])
            y = (u_sum - y_sum) / self.y_cos[-1]

            self.output = self.ys[-1] = y
        return self.output

    def change_state(self, x):
        return 0

    def derivative(self, e):
        return 0


class Diagram:
    def __init__(self, blocks, sums, inputs):
        """Create a diagram

        :param blocks: list of blocks
        :param sums: sums specified as dictionaries with keys equal to output signal and values as tuples of strings of the form "<sign><signal>"
        :param inputs: inputs specified as dictionaries with keys equal to signal names and values functions of time


        Example

        >>> blocks = [Gc, G]
        >>> sums = {'e': ('+ysp', '-y')}
        >>> inputs = {'ysp': step()}
        >>> diagram = Diagram(blocks, sums, inputs)

        """
        if not all(isinstance(block, Block) for block in blocks):
            raise TypeError("blocks must be a list of blocks")
        for output, cinputs in sums.items():
            for s in cinputs:
                if s[0] not in "+-":
                    raise ValueError(
                        f"In the sum '{output}': {cinputs}, there is no sign for '{s}'"
                    )

        self.blocks = blocks
        self.sums = sums
        self.inputs = inputs
        self.reset()

    def reset(self):
        self.signals = {b.inputname: 0 for b in self.blocks}
        self.signals.update({b.outputname: 0 for b in self.blocks})
        self.signals.update({output: 0 for output in self.sums})
        for block in self.blocks:
            block.reset()

    def step(self, t, dt):
        signals = self.signals
        # Evaluate all inputs
        for signal, function in self.inputs.items():
            signals[signal] = function(t)
        # Evaluate sums
        for output, inputs in self.sums.items():
            signals[output] = sum(int(s[0] + "1") * signals[s[1:]] for s in inputs)
        # Evaluate blocks and integrate
        for block in self.blocks:
            u = signals[block.inputname]
            signals[block.outputname] = block.change_input(t, u)
            block.change_state(block.state + block.derivative(u) * dt)
        return signals

    def simulate(self, ts, progress=False):
        """Simulate diagram

        :param ts: iterable, timesteps to simulate. Note this should be equally spaced
        :param progress: display progress bar

        Returns dictionary with keys for each signal in the diagram and values an iterable of values
        """

        if progress:
            from tqdm.auto import tqdm as tqdm

            pbar = tqdm(total=len(ts))
        dt = ts[1]
        outputs = defaultdict(list)
        self.reset()
        for t in ts:
            newoutputs = self.step(t, dt)
            for signal, value in newoutputs.items():
                outputs[signal].append(value)
            if progress:
                pbar.update()
        return outputs

    def __repr__(self):
        return "\n".join(str(b) for b in self.blocks)


# Input functions
def step(initial=0, starttime=0, size=1):
    """Return a function which can be used to simulate a step"""

    def stepfunction(t):
        if t < starttime:
            return initial
        else:
            return initial + size

    return stepfunction


def zero(t):
    """This function returns zero for all time"""
    return 0


def simple_control_diagram(Gc, G, Gd=None, Gm=None, ysp=step(), d=zero):
    """Construct a simple control diagram for quick controller simulations

                                     | d
                                  ┌─────┐
                                  │ Gd  │
                                  └──┬──┘
                                     │ yd
    ysp +  e ┌──────┐  u  ┌─────┐ yu v     y
       ──>o─>│  Gc  ├────>│  G  ├───>o─┬──>
         ─↑  └──────┘     └─────┘      │
          │                            │
          │  ym     ┌─────┐            │
          └─────────┤ Gm  │<───────────┘
                    └─────┘


       Required arguments:

       Gc: Controller, a blocksim.Block with name='Gc', input='e', output='u'
       G: System, a blocksim.Block with name='G', input='u', output='yu'

       Optional arguments:
       Gd: disturbance response, a blocksim.Block with name='Gd', input='d', output='yd'
       ysp: a function of time to represent the input
       d: a function of time to represent the disturbance

       Returns

       blocksim.Diagram object representing this diagram

    """

    if Gd is None:
        Gd = Zero("Gd", "d", "yd")

    if Gm is None:
        Gm = LTI("Gm", "y", "ym", 1)

    blocks = [Gc, G, Gd, Gm]

    sums = {"e": ("+ysp", "-ym"), "y": ("+yu", "+yd")}

    inputs = {"ysp": ysp, "d": d}

    return Diagram(blocks, sums, inputs)
